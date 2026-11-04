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
 * Entry point. With no job manifest present the mod does nothing at all, so the
 * instance stays usable by hand for setting up the template world.
 */
public final class McwallCapture implements ClientModInitializer {

    public static final String STATE_DIR = "mcwall";
    private static final Logger LOGGER = LoggerFactory.getLogger("mcwall-capture");

    private static volatile BatchRunner runner;

    @Override
    public void onInitializeClient() {
        Path gameDir = FabricLoader.getInstance().getGameDir();
        Path stateDir = gameDir.resolve(STATE_DIR);
        Path jobs = stateDir.resolve("jobs.jsonl");

        if (!Files.exists(jobs)) {
            LOGGER.info("mcwall: no {} — idle, the game behaves normally", jobs);
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
