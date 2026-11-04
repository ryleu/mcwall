package me.ryleu.mcwall.capture;

import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import com.mojang.blaze3d.platform.NativeImage;
import net.minecraft.client.Minecraft;
import net.minecraft.client.Screenshot;
import net.minecraft.client.multiplayer.ClientLevel;
import net.minecraft.core.BlockPos;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.level.GameRules;
import net.minecraft.world.level.GameType;
import net.minecraft.world.level.block.Blocks;
import net.minecraft.world.level.levelgen.Heightmap;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Deque;
import java.util.List;
import java.util.Set;

/**
 * Walks a job manifest, sets the world to each state, captures, and exits.
 *
 * <p>State setting and the capture loop are kept apart on purpose: mode 2 will
 * add a sky-override step to {@link #applyJob} without touching the frame
 * counting or the writing below it.
 */
public final class BatchRunner {

    private static final Logger LOGGER = LoggerFactory.getLogger("mcwall-capture");

    private enum Phase {
        BOOT,
        SWITCH_WORLD,
        SOAK,
        APPLY,
        SETTLE,
        WARMUP,
        AWAIT_CAPTURE,
        DONE
    }

    private final Path stateDir;
    private final RunConfig config;
    private final Deque<Job> pending = new ArrayDeque<>();
    private final int totalJobs;

    private Phase phase = Phase.BOOT;
    private Job current;
    private String loadedWorld;
    private String loadedSubSeason;
    private Job.Camera loadedCamera;
    /** Camera anchor found by the biome search, or null when not searching. */
    private BlockPos anchor;
    private int stableTicks;
    private int lastLoadedChunks = -1;
    private int lastRenderedSections = -1;
    private int framesSincePhase;
    private int ticksInPhase;
    private int completed;
    private int skipped;
    private int failed;
    private long batchStartedAt;

    /** Set from the client thread, read and cleared on the render thread. */
    private volatile boolean captureRequested;
    private volatile Path captureTarget;
    private volatile boolean captureDone;
    private volatile String captureError;

    public BatchRunner(Path stateDir, RunConfig config, List<Job> jobs) {
        this.stateDir = stateDir;
        this.config = config;
        this.pending.addAll(jobs);
        this.totalJobs = jobs.size();
        this.batchStartedAt = System.currentTimeMillis();
    }

    public static BatchRunner load(Path stateDir, Path gameDir) throws IOException {
        RunConfig config = RunConfig.load(stateDir.resolve("run.json"), gameDir);
        List<Job> jobs = new ArrayList<>();
        for (String line : Files.readAllLines(stateDir.resolve("jobs.jsonl"), StandardCharsets.UTF_8)) {
            String trimmed = line.trim();
            if (trimmed.isEmpty() || trimmed.startsWith("#")) {
                continue;
            }
            JsonObject json = JsonParser.parseString(trimmed).getAsJsonObject();
            jobs.add(Job.fromJson(json));
        }
        LOGGER.info("mcwall: loaded {} jobs from {}", jobs.size(), stateDir);
        return new BatchRunner(stateDir, config, jobs);
    }

    // ---------------------------------------------------------------- ticking

    public void tick(Minecraft minecraft) {
        if (phase == Phase.DONE) {
            return;
        }
        if (stopRequested()) {
            LOGGER.info("mcwall: stop file seen, finishing after the current job");
            finish(minecraft, "stopped");
            return;
        }

        ticksInPhase++;
        switch (phase) {
            case BOOT -> boot(minecraft);
            case SWITCH_WORLD -> switchWorld(minecraft);
            case SOAK -> soakSnow(minecraft);
            case APPLY -> applyJob(minecraft);
            case SETTLE, WARMUP, AWAIT_CAPTURE -> advanceCapture(minecraft);
            case DONE -> {
            }
        }
    }

    /** Wait for resource loading to finish, then take the first job. */
    private void boot(Minecraft minecraft) {
        if (minecraft.getOverlay() != null) {
            return;
        }
        if (minecraft.level != null && minecraft.getSingleplayerServer() != null) {
            // Launched straight into a world (Prism --world).
            loadedWorld = currentWorldName(minecraft);
        }
        LOGGER.info("mcwall: starting batch of {} jobs (world '{}')", totalJobs, loadedWorld);
        nextJob(minecraft);
    }

    /** Options that must hold for every capture, set once the world is up. */
    private void prepareClient(Minecraft minecraft) {
        minecraft.options.hideGui = true;
        minecraft.options.pauseOnLostFocus = false;
        if (minecraft.options.renderDistance().get() != config.renderDistance) {
            minecraft.options.renderDistance().set(config.renderDistance);
            minecraft.levelRenderer.allChanged();
        }
        if (minecraft.screen != null) {
            minecraft.setScreen(null);
        }
    }

    /**
     * Has the world finished generating, loading and meshing around the camera?
     *
     * <p>A frame count is not good enough: at render distance 16 a fresh camera
     * spot has thousands of chunks to generate, and capturing early gives a
     * frame with a wall of fog where terrain should be. Three signals have to
     * agree, and then hold steady — the server's queue has to be empty, and
     * neither the client's loaded-chunk count nor its rendered-section count
     * may still be moving. Sodium replaces the section renderer, so the counts
     * are used only for stability, never compared against an expected total.
     */
    private boolean terrainReady(Minecraft minecraft) {
        ClientLevel level = minecraft.level;
        if (level == null || minecraft.player == null) {
            stableTicks = 0;
            return false;
        }
        MinecraftServer server = minecraft.getSingleplayerServer();
        if (server != null && server.overworld().getChunkSource().getPendingTasksCount() > 0) {
            stableTicks = 0;
            return false;
        }
        int loaded = level.getChunkSource().getLoadedChunksCount();
        int rendered = renderedSections(minecraft);
        if (loaded != lastLoadedChunks || rendered != lastRenderedSections) {
            lastLoadedChunks = loaded;
            lastRenderedSections = rendered;
            stableTicks = 0;
            return false;
        }
        stableTicks++;
        return stableTicks >= config.terrainStableTicks;
    }

    /**
     * Rendered section count, or 0 if the renderer will not say.
     *
     * <p>Sodium replaces the section renderer, so this is third-party code on a
     * path that runs every tick. It is only one of three readiness signals, and
     * only its stability matters, so a constant 0 degrades gracefully to the
     * server queue and loaded-chunk count.
     */
    private static int renderedSections(Minecraft minecraft) {
        try {
            return minecraft.levelRenderer.countRenderedSections();
        } catch (RuntimeException exception) {
            return 0;
        }
    }

    private void nextJob(Minecraft minecraft) {
        while (true) {
            current = pending.poll();
            if (current == null) {
                finish(minecraft, "complete");
                return;
            }
            Path output = config.outputRoot.resolve(current.output());
            if (Files.exists(output)) {
                // Resume: an interrupted batch picks up where it stopped.
                skipped++;
                continue;
            }
            break;
        }

        if (!current.world().equals(loadedWorld)) {
            setPhase(Phase.SWITCH_WORLD);
        } else {
            setPhase(Phase.APPLY);
        }
    }

    private void switchWorld(Minecraft minecraft) {
        if (ticksInPhase == 1) {
            LOGGER.info("mcwall: loading world variant '{}'", current.world());
            if (minecraft.level != null) {
                minecraft.level.disconnect();
                minecraft.disconnect();
            }
            return;
        }
        if (ticksInPhase == 5) {
            String world = current.world();
            if (minecraft.getLevelSource().levelExists(world)) {
                minecraft.createWorldOpenFlows().openWorld(world, () ->
                        LOGGER.error("mcwall: could not open world '{}'", world));
            } else if (world.equals(config.baseWorld) || config.baseWorld.isEmpty()) {
                LOGGER.info("mcwall: creating template world '{}'", world);
                WorldTemplate.create(minecraft, world);
            } else {
                // Variants are copies of the base world, made by the
                // orchestrator. Generating one here would use this mod's own
                // seed, and terrain that silently disagrees with the rest of
                // the library is worse than a failed job.
                LOGGER.error("mcwall: world '{}' is missing and is not the base world "
                        + "'{}'; the orchestrator derives variants by copying, so rerun "
                        + "the render rather than generating it here",
                        world, config.baseWorld);
                failed++;
                nextJob(minecraft);
            }
            return;
        }
        if (minecraft.level != null && minecraft.player != null
                && minecraft.getSingleplayerServer() != null) {
            prepareClient(minecraft);
            loadedWorld = currentWorldName(minecraft);
            loadedSubSeason = null; // a fresh world needs its season set again
            loadedCamera = null;
            locateAnchor(minecraft);
            setPhase(needsSoak() ? Phase.SOAK : Phase.APPLY);
            return;
        }
        if (ticksInPhase > config.jobTimeoutTicks) {
            LOGGER.error("mcwall: timed out loading world '{}'", current.world());
            failed++;
            loadedWorld = null;
            nextJob(minecraft);
        }
    }

    /**
     * Anchor the camera in a biome whose foliage changes with the season.
     *
     * <p>Runs once per world load. Only x and z are taken from the result; the
     * height and heading stay as configured, so a camera sweep still works.
     */
    private void locateAnchor(Minecraft minecraft) {
        if (config.locateRadius <= 0) {
            return;
        }
        MinecraftServer server = minecraft.getSingleplayerServer();
        if (server == null) {
            return;
        }
        BiomeLocator.find(server.overworld(), config.locateRadius, config.locateBiome)
                .ifPresent(spot -> {
                    anchor = spot;
                    writeLocated(spot);
                });
    }

    private void writeLocated(BlockPos spot) {
        JsonObject located = new JsonObject();
        located.addProperty("x", spot.getX());
        located.addProperty("surface_y", spot.getY());
        located.addProperty("z", spot.getZ());
        try {
            Files.writeString(stateDir.resolve("located.json"), located.toString());
        } catch (IOException exception) {
            LOGGER.warn("mcwall: could not write located.json", exception);
        }
    }

    /** Job camera, re-anchored onto the located biome when there is one. */
    private Job.Camera cameraFor(Job job) {
        Job.Camera camera = job.camera() != null ? job.camera() : config.camera();
        if (anchor == null) {
            return camera;
        }
        return new Job.Camera(
                anchor.getX() + 0.5, camera.y(), anchor.getZ() + 0.5,
                camera.yaw(), camera.pitch());
    }

    /** A "-snow" world that has not been snowed on yet. */
    private boolean needsSoak() {
        return current != null
                && current.world().endsWith("-snow")
                && config.snowSoakTicks > 0
                && !Files.exists(soakMarker(current.world()));
    }

    private Path soakMarker(String world) {
        return stateDir.resolve("soaked-" + world);
    }

    /**
     * Let it snow until the ground is covered, once per snow world.
     *
     * <p>Serene Seasons turns winter precipitation into snowfall in temperate
     * biomes, but snow *layers* only form on random block ticks — and the grid
     * renders with randomTickSpeed at 0 so nothing drifts between captures. So
     * the snow world gets one soak at a high tick speed before any capture,
     * and the result is saved with the world.
     */
    private void soakSnow(Minecraft minecraft) {
        MinecraftServer server = minecraft.getSingleplayerServer();
        if (server == null) {
            loadedWorld = null;
            setPhase(Phase.SWITCH_WORLD);
            return;
        }

        if (ticksInPhase == 1) {
            LOGGER.info("mcwall: preparing snow cover in '{}'", current.world());
            Job.Camera camera = cameraFor(current);
            server.execute(() -> {
                run(server, "gamerule doSeasonCycle false");
                run(server, "season set " + config.snowSeason);
                for (ServerPlayer player : server.getPlayerList().getPlayers()) {
                    player.setGameMode(GameType.SPECTATOR);
                    player.teleportTo(
                            server.overworld(),
                            camera.x(), camera.y(), camera.z(),
                            Set.of(), camera.yaw(), camera.pitch(), true);
                }
                for (ServerLevel level : server.getAllLevels()) {
                    level.setWeatherParameters(0, Integer.MAX_VALUE, true, false);
                    level.setRainLevel(1.0F);
                }
            });
            loadedSubSeason = config.snowSeason;
            loadedCamera = camera;
            return;
        }

        // The chunks have to be there before anything can be put on them.
        if (!terrainReady(minecraft) && ticksInPhase < config.snowSoakTicks) {
            return;
        }

        placeSnow(server, cameraFor(current));
        try {
            Files.writeString(soakMarker(current.world()), "soaked\n");
        } catch (IOException exception) {
            LOGGER.warn("mcwall: could not write the soak marker", exception);
        }
        setPhase(Phase.APPLY);
    }

    /**
     * Lay snow on every surface around the camera that will hold it.
     *
     * <p>Vanilla's own snowfall only touches one random column per chunk per
     * 16 ticks, so covering the ground within render distance would take about
     * 17 minutes of wall clock. Placing it directly takes a second and gives
     * complete, repeatable cover. Only columns the biome would snow on are
     * touched, so nothing melts back out between captures.
     */
    private void placeSnow(MinecraftServer server, Job.Camera camera) {
        ServerLevel level = server.overworld();
        BlockPos centre = BlockPos.containing(camera.x(), camera.y(), camera.z());
        int radius = config.renderDistance * 16 + 32;
        int placed = 0;
        int skipped = 0;

        for (int dx = -radius; dx <= radius; dx++) {
            for (int dz = -radius; dz <= radius; dz++) {
                BlockPos column = centre.offset(dx, 0, dz);
                if (!level.hasChunkAt(column)) {
                    skipped++;
                    continue;
                }
                BlockPos top = level.getHeightmapPos(Heightmap.Types.MOTION_BLOCKING, column);
                if (!level.getBlockState(top).isAir()) {
                    continue;
                }
                if (!level.getBiome(top).value().shouldSnow(level, top)) {
                    continue;
                }
                if (!Blocks.SNOW.defaultBlockState().canSurvive(level, top)) {
                    continue;
                }
                level.setBlockAndUpdate(top, Blocks.SNOW.defaultBlockState());
                placed++;
            }
        }
        run(server, "save-all");
        LOGGER.info("mcwall: laid {} snow layers within {} blocks ({} columns unloaded)",
                placed, radius, skipped);
    }

    // ------------------------------------------------------------ state setup

    /**
     * Put the world into the job's state. Everything here runs on the
     * integrated server; nothing captures.
     */
    private void applyJob(Minecraft minecraft) {
        MinecraftServer server = minecraft.getSingleplayerServer();
        if (server == null) {
            // The world went away under us; reload it and try the job again.
            loadedWorld = null;
            setPhase(Phase.SWITCH_WORLD);
            return;
        }

        boolean seasonChanged = !current.subSeason().equals(loadedSubSeason);
        Job.Camera wantedCamera = cameraFor(current);
        // A camera move needs terrain to load and generate at the new spot,
        // which takes as long as a season change does.
        boolean cameraMoved = !wantedCamera.equals(loadedCamera);
        Job job = current;

        server.execute(() -> {
            // Nothing may advance on its own between captures.
            GameRules rules = server.getGameRules();
            setRule(server, rules, GameRules.RULE_DAYLIGHT, false);
            setRule(server, rules, GameRules.RULE_WEATHER_CYCLE, false);
            setRule(server, rules, GameRules.RULE_DOMOBSPAWNING, false);
            setIntRule(server, rules, GameRules.RULE_RANDOMTICKING, 0);
            run(server, "gamerule doSeasonCycle false");
            run(server, "difficulty peaceful");

            if (seasonChanged) {
                run(server, "season set " + job.subSeason());
            }

            // Move the camera through the API, not through /tp. A command run
            // from the server's own source stack has no executing entity, so
            // "@s" matches nothing and the teleport silently does nothing.
            Job.Camera camera = cameraFor(job);
            for (ServerPlayer player : server.getPlayerList().getPlayers()) {
                player.setGameMode(GameType.SPECTATOR);
                player.teleportTo(
                        server.overworld(),
                        camera.x(), camera.y(), camera.z(),
                        Set.of(), // absolute, not relative
                        camera.yaw(), camera.pitch(),
                        true);
            }

            for (ServerLevel level : server.getAllLevels()) {
                level.setDayTime(job.tick());
                // Force the weather state outright instead of waiting out the
                // vanilla fade, which takes thousands of ticks.
                level.setWeatherParameters(
                        job.raining() ? 0 : Integer.MAX_VALUE,
                        job.raining() ? Integer.MAX_VALUE : 0,
                        job.raining(),
                        job.thundering());
                level.setRainLevel(job.raining() ? 1.0F : 0.0F);
                level.setThunderLevel(job.thundering() ? 1.0F : 0.0F);
            }
        });

        loadedCamera = wantedCamera;
        if (seasonChanged || cameraMoved) {
            loadedSubSeason = current.subSeason();
            // Foliage colour lives in the chunk meshes, so they must be rebuilt.
            minecraft.levelRenderer.allChanged();
            setPhase(Phase.SETTLE);
        } else {
            // Only a season change needs the long settle. Jobs that merely move
            // the clock or the weather go straight to the shader warm-up, which
            // is most of the grid: 143 of every 144 jobs.
            setPhase(Phase.WARMUP);
        }
    }

    private static void setRule(MinecraftServer server, GameRules rules,
                                GameRules.Key<GameRules.BooleanValue> key, boolean value) {
        rules.getRule(key).set(value, server);
    }

    private static void setIntRule(MinecraftServer server, GameRules rules,
                                   GameRules.Key<GameRules.IntegerValue> key, int value) {
        rules.getRule(key).set(value, server);
    }

    private static void run(MinecraftServer server, String command) {
        server.getCommands().performPrefixedCommand(server.createCommandSourceStack(), command);
    }

    // ------------------------------------------------------------- capturing

    private void advanceCapture(Minecraft minecraft) {
        // The client level fades rain in and out on its own; pin it every tick
        // so a capture can never land mid-fade.
        ClientLevel level = minecraft.level;
        if (level != null) {
            level.setRainLevel(current.raining() ? 1.0F : 0.0F);
            level.setThunderLevel(current.thundering() ? 1.0F : 0.0F);
        }

        if (phase == Phase.SETTLE) {
            if (terrainReady(minecraft)) {
                setPhase(Phase.WARMUP);
            } else if (framesSincePhase > config.seasonSettleFrames) {
                LOGGER.warn("mcwall: terrain still not settled after {} frames, "
                        + "capturing {} anyway", framesSincePhase, current.id());
                setPhase(Phase.WARMUP);
            }
            return;
        }
        if (phase == Phase.WARMUP && framesSincePhase >= config.warmupFrames) {
            captureTarget = config.outputRoot.resolve(current.output());
            captureDone = false;
            captureError = null;
            captureRequested = true;
            setPhase(Phase.AWAIT_CAPTURE);
            return;
        }
        if (phase == Phase.AWAIT_CAPTURE) {
            if (captureDone) {
                if (captureError != null) {
                    LOGGER.error("mcwall: capture {} failed: {}", current.id(), captureError);
                    failed++;
                } else {
                    completed++;
                }
                writeStatus(false);
                nextJob(minecraft);
                return;
            }
            if (ticksInPhase > config.jobTimeoutTicks) {
                LOGGER.error("mcwall: capture {} timed out", current.id());
                failed++;
                captureRequested = false;
                nextJob(minecraft);
            }
        }
    }

    /**
     * Called at the end of every rendered frame, on the render thread.
     *
     * <p>This is the only place a capture may happen: the main render target
     * holds Iris's final composite exactly here.
     */
    public void afterFrame() {
        framesSincePhase++;
        if (!captureRequested) {
            return;
        }
        captureRequested = false;
        Path target = captureTarget;
        try {
            Minecraft minecraft = Minecraft.getInstance();
            try (NativeImage image = Screenshot.takeScreenshot(minecraft.getMainRenderTarget())) {
                Files.createDirectories(target.getParent());
                // Write beside the target and rename, so a killed batch never
                // leaves a half-written PNG that resume would skip.
                Path temporary = target.resolveSibling(target.getFileName() + ".part");
                image.writeToFile(temporary);
                Files.move(temporary, target, StandardCopyOption.ATOMIC_MOVE,
                        StandardCopyOption.REPLACE_EXISTING);
            }
        } catch (Exception exception) {
            captureError = exception.toString();
        } finally {
            captureDone = true;
        }
    }

    // --------------------------------------------------------------- plumbing

    private void setPhase(Phase next) {
        phase = next;
        framesSincePhase = 0;
        ticksInPhase = 0;
        if (next == Phase.SETTLE) {
            stableTicks = 0;
            lastLoadedChunks = -1;
            lastRenderedSections = -1;
        }
    }

    private String currentWorldName(Minecraft minecraft) {
        MinecraftServer server = minecraft.getSingleplayerServer();
        return server == null ? null : server.getWorldData().getLevelName();
    }

    private boolean stopRequested() {
        return Files.exists(stateDir.resolve("STOP"));
    }

    private void finish(Minecraft minecraft, String reason) {
        setPhase(Phase.DONE);
        writeStatus(true);
        LOGGER.info("mcwall: batch {} — {} captured, {} already present, {} failed",
                reason, completed, skipped, failed);
        try {
            Files.writeString(stateDir.resolve("DONE"), reason + "\n");
        } catch (IOException exception) {
            LOGGER.error("mcwall: could not write DONE", exception);
        }
        if (config.quitWhenDone) {
            minecraft.stop();
        }
    }

    /** Heartbeat for the orchestrator's watchdog, rewritten after every job. */
    private void writeStatus(boolean done) {
        JsonObject status = new JsonObject();
        status.addProperty("total", totalJobs);
        status.addProperty("completed", completed);
        status.addProperty("skipped", skipped);
        status.addProperty("failed", failed);
        status.addProperty("remaining", pending.size());
        status.addProperty("done", done);
        status.addProperty("updated_at", System.currentTimeMillis());
        status.addProperty("elapsed_ms", System.currentTimeMillis() - batchStartedAt);
        if (current != null) {
            status.addProperty("current", current.id());
        }
        try {
            Path target = stateDir.resolve("status.json");
            Path temporary = stateDir.resolve("status.json.part");
            Files.writeString(temporary, status.toString());
            Files.move(temporary, target, StandardCopyOption.REPLACE_EXISTING);
        } catch (IOException exception) {
            LOGGER.warn("mcwall: could not write status", exception);
        }
    }
}
