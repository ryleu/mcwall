package me.ryleu.mcwall.capture;

import net.fabricmc.api.ClientModInitializer;
import net.fabricmc.fabric.api.client.event.lifecycle.v1.ClientTickEvents;
import net.fabricmc.loader.api.FabricLoader;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;

/**
 * Entry point. The mod does nothing unless the orchestrator asks for a batch,
 * so this instance stays an ordinary game to open, fly around and set a shot
 * up in.
 */
public final class McwallCapture implements ClientModInitializer {

    public static final String STATE_DIR = "mcwall";
    /**
     * Set by the orchestrator when it launches the game to render.
     *
     * <p>The gate is an environment variable rather than the presence of a job
     * manifest, so that opening this instance by hand — to pick a spot, frame
     * a shot, or just look around — can never start a batch and quit the game
     * out from under whoever is using it.
     */
    public static final String CAPTURE_ENV = "MCWALL_CAPTURE";
    private static final Logger LOGGER = LoggerFactory.getLogger("mcwall-capture");

    private static volatile BatchRunner runner;

    @Override
    public void onInitializeClient() {
        if (!"1".equals(System.getenv(CAPTURE_ENV))) {
            LOGGER.info("mcwall: {} is not set — idle, the game behaves normally",
                    CAPTURE_ENV);
            return;
        }

        Path gameDir = FabricLoader.getInstance().getGameDir();
        Path stateDir = gameDir.resolve(STATE_DIR);
        Path jobs = stateDir.resolve("jobs.jsonl");

        if (!Files.exists(jobs)) {
            LOGGER.error("mcwall: {} is set but there is no {}", CAPTURE_ENV, jobs);
            return;
        }

        try {
            runner = BatchRunner.load(stateDir, gameDir);
        } catch (IOException exception) {
            LOGGER.error("mcwall: could not read the job manifest", exception);
            return;
        }

        ClientTickEvents.END_CLIENT_TICK.register(minecraft -> {
            BatchRunner active = runner;
            if (active != null) {
                active.tick(minecraft);
            }
        });
    }

    /** The live batch, or null when the game was launched without a manifest. */
    public static BatchRunner runner() {
        return runner;
    }
}
