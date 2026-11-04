package me.ryleu.mcwall.capture;

import com.google.gson.JsonObject;
import com.google.gson.JsonParser;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;

/** Everything the orchestrator tells the mod about this batch. */
public final class RunConfig {

    public final Path outputRoot;
    /** Frames rendered after a state change before the capture is taken. */
    public final int warmupFrames;
    /** Upper bound on the wait for terrain; the real gate is terrainReady(). */
    public final int seasonSettleFrames;
    /** Ticks the chunk counts must hold steady before terrain counts as ready. */
    public final int terrainStableTicks;
    public final int renderDistance;
    /** Give up on a job after this many ticks and move on. */
    public final int jobTimeoutTicks;
    public final boolean quitWhenDone;
    private final Job.Camera camera;
    /** Ticks to let snow fall in a "-snow" world before capturing in it. */
    public final int snowSoakTicks;
    /** randomTickSpeed during the soak; snow layers form on random ticks. */
    public final int snowRandomTickSpeed;
    /** Sub-season to soak under, which must be a winter one. */
    public final String snowSeason;
    /** Biome ids to anchor the camera in, or empty to use the camera as given. */
    public final java.util.List<String> locateBiome;
    public final int locateRadius;

    private RunConfig(JsonObject json, Path gameDir) {
        String root = optString(json, "output_root", "mcwall/out");
        Path candidate = Path.of(root);
        this.outputRoot = candidate.isAbsolute() ? candidate : gameDir.resolve(candidate);
        this.warmupFrames = optInt(json, "warmup_frames", 120);
        this.seasonSettleFrames = optInt(json, "season_settle_frames", 200);
        this.terrainStableTicks = optInt(json, "terrain_stable_ticks", 40);
        this.renderDistance = optInt(json, "render_distance", 16);
        this.jobTimeoutTicks = optInt(json, "job_timeout_ticks", 1200);
        this.quitWhenDone = !json.has("quit_when_done") || json.get("quit_when_done").getAsBoolean();
        this.snowSoakTicks = optInt(json, "snow_soak_ticks", 2400);
        this.snowRandomTickSpeed = optInt(json, "snow_random_tick_speed", 120);
        this.snowSeason = optString(json, "snow_season", "mid_winter");

        java.util.List<String> biomes = new java.util.ArrayList<>();
        if (json.has("locate_biome")) {
            JsonObject locate = json.getAsJsonObject("locate_biome");
            this.locateRadius = optInt(locate, "radius", 6000);
            if (locate.has("prefer")) {
                locate.getAsJsonArray("prefer").forEach(e -> biomes.add(e.getAsString()));
            }
        } else {
            this.locateRadius = 0;
        }
        this.locateBiome = java.util.List.copyOf(biomes);

        JsonObject camera = json.has("camera") ? json.getAsJsonObject("camera") : new JsonObject();
        this.camera = new Job.Camera(
                optDouble(camera, "x", 0.5),
                optDouble(camera, "y", 120.0),
                optDouble(camera, "z", 0.5),
                (float) optDouble(camera, "yaw", -90.0),
                (float) optDouble(camera, "pitch", -10.0));
    }

    /** Camera used by any job that does not carry its own override. */
    public Job.Camera camera() {
        return camera;
    }

    public static RunConfig load(Path path, Path gameDir) throws IOException {
        JsonObject json = Files.exists(path)
                ? JsonParser.parseString(Files.readString(path)).getAsJsonObject()
                : new JsonObject();
        return new RunConfig(json, gameDir);
    }

    private static String optString(JsonObject json, String key, String fallback) {
        return json.has(key) ? json.get(key).getAsString() : fallback;
    }

    private static int optInt(JsonObject json, String key, int fallback) {
        return json.has(key) ? json.get(key).getAsInt() : fallback;
    }

    private static double optDouble(JsonObject json, String key, double fallback) {
        return json.has(key) ? json.get(key).getAsDouble() : fallback;
    }
}
