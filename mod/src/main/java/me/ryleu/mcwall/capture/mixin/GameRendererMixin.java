package me.ryleu.mcwall.capture.mixin;

import me.ryleu.mcwall.capture.BatchRunner;
import me.ryleu.mcwall.capture.McwallCapture;
import net.minecraft.client.DeltaTracker;
import net.minecraft.client.renderer.GameRenderer;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

/**
 * Counts rendered frames and takes the capture at the one moment it is valid.
 *
 * <p>At the tail of {@code GameRenderer.render} the main render target holds
 * the finished frame, which under Iris is the output of its final composite
 * pass. Reading it anywhere earlier would capture the unshaded scene.
 */
@Mixin(GameRenderer.class)
public class GameRendererMixin {

    @Inject(method = "render(Lnet/minecraft/client/DeltaTracker;Z)V", at = @At("TAIL"))
    private void mcwall$afterFrame(DeltaTracker deltaTracker, boolean renderLevel, CallbackInfo info) {
        BatchRunner runner = McwallCapture.runner();
        if (runner != null) {
            runner.afterFrame();
        }
    }
}
