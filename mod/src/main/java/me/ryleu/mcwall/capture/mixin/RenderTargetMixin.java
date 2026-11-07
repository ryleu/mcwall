package me.ryleu.mcwall.capture.mixin;

import com.mojang.blaze3d.pipeline.RenderTarget;
import me.ryleu.mcwall.capture.HdrCapture;
import org.lwjgl.opengl.GL30;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.ModifyArgs;
import org.spongepowered.asm.mixin.injection.invoke.arg.Args;

/**
 * Allocates offscreen colour attachments as {@code RGBA16F} instead of
 * {@code RGBA8}, so gradients survive to the capture.
 *
 * <p>Adapted from rrtt217's HDR Mod (MIT), which publishes 1.20-1.21.1 and
 * 1.21.9 upward with nothing covering 1.21.4. Only the format promotion is
 * taken: the rest of that mod drives an HDR swapchain, and this renders into
 * a headless compositor that has none.
 *
 * <p>{@code createBuffers} allocates the depth attachment from the same
 * method, hence the guard — {@code GL_DEPTH_COMPONENT} must be left alone.
 */
@Mixin(RenderTarget.class)
public class RenderTargetMixin {

    @ModifyArgs(
            method = "createBuffers",
            at = @At(
                    value = "INVOKE",
                    target = "Lcom/mojang/blaze3d/platform/GlStateManager;"
                            + "_texImage2D(IIIIIIIILjava/nio/IntBuffer;)V"))
    private void mcwall$promoteColourAttachment(Args args) {
        if (!HdrCapture.enabled()) {
            return;
        }
        if (args.<Integer>get(2) == GL30.GL_RGBA8) {
            args.set(2, GL30.GL_RGBA16F);
            args.set(7, GL30.GL_HALF_FLOAT);
        }
    }
}
