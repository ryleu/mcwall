package me.ryleu.mcwall.capture;

import com.mojang.datafixers.util.Pair;
import net.minecraft.core.BlockPos;
import net.minecraft.core.Holder;
import net.minecraft.core.registries.Registries;
import net.minecraft.resources.ResourceLocation;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.world.level.biome.Biome;
import net.minecraft.world.level.levelgen.Heightmap;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.util.List;
import java.util.Optional;

/**
 * Finds a camera spot in a biome whose foliage actually changes with the season.
 *
 * <p>Spawn is wherever the seed puts it, which here was spruce taiga. Serene
 * Seasons recolours temperate broadleaf foliage strongly and conifers barely at
 * all, so a taiga camera would make all twelve sub-seasons look the same except
 * for winter snow — twelve times the library for no visible difference.
 */
public final class BiomeLocator {

    private static final Logger LOGGER = LoggerFactory.getLogger("mcwall-capture");

    /** Searched in order; the first one found within range wins. */
    private static final List<String> DEFAULT_PREFERENCES = List.of(
            "minecraft:forest",
            "minecraft:flower_forest",
            "minecraft:birch_forest",
            "minecraft:dark_forest",
            "minecraft:plains",
            "minecraft:meadow");

    private BiomeLocator() {
    }

    /**
     * Closest position in one of {@code preferences}, or empty if none is near.
     *
     * @param radius blocks to search outward from the origin
     */
    public static Optional<BlockPos> find(
            ServerLevel level, BlockPos origin, int radius, List<String> preferences) {
        List<String> wanted = preferences.isEmpty() ? DEFAULT_PREFERENCES : preferences;

        for (String id : wanted) {
            ResourceLocation key = ResourceLocation.tryParse(id);
            if (key == null) {
                LOGGER.warn("mcwall: '{}' is not a valid biome id, skipping", id);
                continue;
            }
            Pair<BlockPos, Holder<Biome>> found = level.findClosestBiome3d(
                    holder -> holder.unwrapKey()
                            .map(resourceKey -> resourceKey.location().equals(key))
                            .orElse(false),
                    origin,
                    radius,
                    32,  // sample step
                    64); // vertical step
            if (found != null) {
                BlockPos at = found.getFirst();
                // Force the chunk to exist before asking for its height: the
                // biome search works on the generator's noise, not on loaded
                // chunks, so in a brand new world the heightmap is still empty
                // and would answer with the world floor.
                level.getChunk(at.getX() >> 4, at.getZ() >> 4);
                // Put the camera on the surface, not at the sample's y.
                int surface = level.getHeight(Heightmap.Types.MOTION_BLOCKING_NO_LEAVES,
                        at.getX(), at.getZ());
                BlockPos spot = new BlockPos(at.getX(), surface, at.getZ());
                LOGGER.info("mcwall: found {} at {} {} {} ({} blocks out)",
                        id, spot.getX(), spot.getY(), spot.getZ(),
                        (int) Math.sqrt(origin.distSqr(spot)));
                return Optional.of(spot);
            }
        }
        LOGGER.warn("mcwall: no preferred biome within {} blocks of {}", radius, origin);
        return Optional.empty();
    }

    public static Optional<BlockPos> find(ServerLevel level, int radius, List<String> preferences) {
        return find(level, BlockPos.ZERO, radius, preferences);
    }
}
