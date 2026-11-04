package me.ryleu.mcwall.capture;

import com.google.gson.JsonObject;
import org.jetbrains.annotations.Nullable;

/**
 * One grid cell to render.
 *
 * <p>Deliberately machine-independent: output paths are relative to the run's
 * output root and nothing here knows about the display side. A mode 2 producer
 * on another host feeds the same mod the same shape of manifest.
 */
public record Job(
        String id,
        String subSeason,
        String world,
        int tick,
        String weather,
        String output,
        @Nullable Camera camera) {

    /** Per-job camera override, used by the scouting and tuning passes. */
    public record Camera(double x, double y, double z, float yaw, float pitch) {
        static Camera fromJson(JsonObject json) {
            return new Camera(
                    json.get("x").getAsDouble(),
                    json.get("y").getAsDouble(),
                    json.get("z").getAsDouble(),
                    json.get("yaw").getAsFloat(),
                    json.get("pitch").getAsFloat());
        }
    }

    public static Job fromJson(JsonObject json) {
        return new Job(
                json.get("id").getAsString(),
                json.get("sub_season").getAsString(),
                json.get("world").getAsString(),
                json.get("tick").getAsInt(),
                json.get("weather").getAsString(),
                json.get("output").getAsString(),
                json.has("camera") ? Camera.fromJson(json.getAsJsonObject("camera")) : null);
    }

    public boolean raining() {
        return weather.equals("rain") || weather.equals("thunder");
    }

    public boolean thundering() {
        return weather.equals("thunder");
    }
}
