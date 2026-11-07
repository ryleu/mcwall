package me.ryleu.mcwall.capture.mixin;

import com.mojang.blaze3d.pipeline.MainTarget;
import me.ryleu.mcwall.capture.HdrCapture;
import org.lwjgl.opengl.GL30;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.ModifyArgs;
import org.spongepowered.asm.mixin.injection.invoke.arg.Args;

/**
 * The same promotion as {@link RenderTargetMixin}, for the one target the
 * capture actually reads.
 *
 * <p>This is separate because {@code MainTarget} does not override {@code
 * createBuffers}: it allocates its colour attachment from its own private
 * {@code allocateColorAttachment}, which the superclass mixin never sees.
 * Promoting only {@code RenderTarget} therefore builds, loads, renders and
 * changes nothing about the captured image — the failure is invisible, so it
 * is worth stating why there are two of these.
 */
@Mixin(MainTarget.class)
public class MainTargetMixin {

    @ModifyArgs(
            method = "allocateColorAttachment",
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
