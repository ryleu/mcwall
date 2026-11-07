package me.ryleu.mcwall.capture;

import com.mojang.blaze3d.pipeline.RenderTarget;
import com.mojang.blaze3d.systems.RenderSystem;
import org.lwjgl.opengl.GL11;
import org.lwjgl.system.MemoryUtil;

import javax.imageio.ImageIO;
import java.awt.Transparency;
import java.awt.color.ColorSpace;
import java.awt.image.BufferedImage;
import java.awt.image.ComponentColorModel;
import java.awt.image.DataBuffer;
import java.awt.image.DataBufferUShort;
import java.awt.image.Raster;
import java.awt.image.WritableRaster;
import java.io.IOException;
import java.nio.FloatBuffer;
import java.nio.file.Path;

/**
 * Reads the finished frame as floating point and writes it 16 bits per
 * channel.
 *
 * <p>A night sky spans about 18 of 255 levels, so the gradient is destroyed by
 * quantisation inside the GPU, before any encoder sees it — which is why no
 * choice of output format or post-hoc dither has ever helped. {@code
 * RenderTargetMixin} and {@code MainTargetMixin} promote the colour
 * attachments to {@code RGBA16F}; this reads one back without going through
 * {@link com.mojang.blaze3d.platform.NativeImage}, which is unsigned-byte in
 * every one of its paths and could not carry the extra bits.
 *
 * <p>The promotion is what matters. If the mixins do not apply, the readback
 * still succeeds — GL converts — and {@link Stats#quantised()} reports 1.0,
 * so a silent fall back to 8 bits shows up as a number rather than as
 * banding noticed five hours later.
 */
public final class HdrCapture {

    private HdrCapture() {
    }

    /**
     * Set by the orchestrator when the batch asked to capture in float.
     *
     * <p>Separate from {@link McwallCapture#CAPTURE_ENV} so that promotion is
     * off for an ordinary launch and off for a batch that did not ask for it.
     * Promotion is meant to be visually neutral, but "meant to be" is not a
     * reason to put it in the path of someone framing a shot by hand.
     */
    public static final String HDR_ENV = "MCWALL_HDR";

    /** Whether to promote the colour attachments and capture in float. */
    public static boolean enabled() {
        return "1".equals(System.getenv(HDR_ENV));
    }

    /** What the float buffer actually contained, for the log. */
    public record Stats(float min, float max, double quantised) {
    }

    /**
     * Write {@code target} to {@code file} as a 16-bit RGB PNG.
     *
     * <p>Values are clamped to 0..1: Complementary tone maps before the final
     * blit, so anything outside that range is not carrying picture. {@link
     * Stats#max()} says whether that assumption held.
     */
    public static Stats writePng16(RenderTarget target, Path file) throws IOException {
        int width = target.width;
        int height = target.height;
        FloatBuffer pixels = MemoryUtil.memAllocFloat(width * height * 4);
        try {
            RenderSystem.bindTexture(target.getColorTextureId());
            GL11.glGetTexImage(GL11.GL_TEXTURE_2D, 0, GL11.GL_RGBA, GL11.GL_FLOAT, pixels);

            WritableRaster raster = Raster.createInterleavedRaster(
                    DataBuffer.TYPE_USHORT, width, height, width * 3, 3,
                    new int[] {0, 1, 2}, null);
            short[] out = ((DataBufferUShort) raster.getDataBuffer()).getData();

            float min = Float.MAX_VALUE;
            float max = -Float.MAX_VALUE;
            long sampled = 0;
            long onByteSteps = 0;

            for (int y = 0; y < height; y++) {
                // GL reads bottom-up and PNG is top-down, the flip NativeImage
                // would have done.
                int source = (height - 1 - y) * width * 4;
                int destination = y * width * 3;
                for (int x = 0; x < width; x++) {
                    for (int channel = 0; channel < 3; channel++) {
                        float value = pixels.get(source + x * 4 + channel);
                        if (value < min) {
                            min = value;
                        }
                        if (value > max) {
                            max = value;
                        }
                        // Every 256th pixel is enough to tell a promoted
                        // attachment from one that is still RGBA8.
                        if ((x & 0xFF) == 0) {
                            sampled++;
                            float steps = value * 255.0F;
                            if (Math.abs(steps - Math.round(steps)) < 1.0e-4F) {
                                onByteSteps++;
                            }
                        }
                        float clamped = Math.min(1.0F, Math.max(0.0F, value));
                        out[destination + x * 3 + channel] =
                                (short) Math.round(clamped * 65535.0F);
                    }
                }
            }

            ComponentColorModel model = new ComponentColorModel(
                    ColorSpace.getInstance(ColorSpace.CS_sRGB), false, false,
                    Transparency.OPAQUE, DataBuffer.TYPE_USHORT);
            BufferedImage image = new BufferedImage(model, raster, false, null);
            if (!ImageIO.write(image, "png", file.toFile())) {
                throw new IOException("no PNG writer accepted a 16-bit raster");
            }

            double quantised = sampled == 0 ? 1.0 : (double) onByteSteps / sampled;
            return new Stats(min, max, quantised);
        } finally {
            MemoryUtil.memFree(pixels);
        }
    }
}
