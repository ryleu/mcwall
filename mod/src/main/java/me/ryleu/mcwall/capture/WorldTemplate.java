package me.ryleu.mcwall.capture;

import net.minecraft.client.Minecraft;
import net.minecraft.world.Difficulty;
import net.minecraft.world.level.GameRules;
import net.minecraft.world.level.LevelSettings;
import net.minecraft.world.level.WorldDataConfiguration;
import net.minecraft.world.level.GameType;
import net.minecraft.world.flag.FeatureFlags;
import net.minecraft.world.level.levelgen.WorldOptions;
import net.minecraft.world.level.levelgen.presets.WorldPresets;

/**
 * Creates the render world when it is missing.
 *
 * <p>The user's own worlds are never touched: the batch only ever opens worlds
 * named in its manifest, inside the dedicated instance's saves directory.
 */
public final class WorldTemplate {

    /** Fixed so every machine that rebuilds the library gets the same terrain. */
    public static final long SEED = 4_815_162_342L;

    private WorldTemplate() {
    }

    public static void create(Minecraft minecraft, String name) {
        GameRules rules = new GameRules(FeatureFlags.DEFAULT_FLAGS);
        rules.getRule(GameRules.RULE_DAYLIGHT).set(false, null);
        rules.getRule(GameRules.RULE_WEATHER_CYCLE).set(false, null);
        rules.getRule(GameRules.RULE_DOMOBSPAWNING).set(false, null);
        rules.getRule(GameRules.RULE_DOENTITYDROPS).set(false, null);
        rules.getRule(GameRules.RULE_RANDOMTICKING).set(0, null);
        rules.getRule(GameRules.RULE_DO_TRADER_SPAWNING).set(false, null);

        LevelSettings settings = new LevelSettings(
                name,
                GameType.SPECTATOR,
                false,
                Difficulty.PEACEFUL,
                true, // commands: the batch drives everything through them
                rules,
                WorldDataConfiguration.DEFAULT);

        minecraft.createWorldOpenFlows().createFreshLevel(
                name,
                settings,
                new WorldOptions(SEED, false, false),
                WorldPresets::createNormalWorldDimensions,
                null);
    }
}
