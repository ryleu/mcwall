self:
{ config, lib, pkgs, ... }:

let
  cfg = config.services.mcwall;
  inherit (lib) mkEnableOption mkOption mkIf types;
in
{
  options.services.mcwall = {
    enable = mkEnableOption "the Minecraft sky wallpaper display path";

    package = mkOption {
      type = types.package;
      default = self.packages.${pkgs.stdenv.hostPlatform.system}.default;
      defaultText = lib.literalExpression "mcwall.packages.\${system}.default";
      description = "The mcwall package providing the CLI.";
    };

    interval = mkOption {
      type = types.str;
      default = "5m";
      description = ''
        How often to re-select the wallpaper. The display path is a few
        milliseconds of Python, and it only calls the wallpaper daemon when the
        chosen asset actually changes.
      '';
    };

    wallpaperDaemon = mkOption {
      type = types.package;
      default = pkgs.swww;
      defaultText = lib.literalExpression "pkgs.swww";
      description = ''
        Package providing the still-wallpaper daemon. Recent nixpkgs ships this
        as awww; mcwall detects either name.
      '';
    };

    enableLoops = mkOption {
      type = types.bool;
      default = false;
      description = "Allow video loops on AC power (needs mpvpaper and a rendered loop set).";
    };

    extraPath = mkOption {
      type = types.listOf types.package;
      default = [ ];
      description = "Extra packages to put on the unit's PATH.";
    };
  };

  config = mkIf cfg.enable {
    home.packages = [ cfg.package cfg.wallpaperDaemon ]
      ++ lib.optional cfg.enableLoops pkgs.mpvpaper;

    systemd.user.services.mcwall = {
      Unit = {
        Description = "Set the Minecraft sky wallpaper for the current moment";
        # Needs a compositor to talk to; never block the session on it.
        After = [ "graphical-session.target" ];
        PartOf = [ "graphical-session.target" ];
      };
      Service = {
        Type = "oneshot";
        ExecStart = "${cfg.package}/bin/mcwall apply";
        Environment = [
          "PATH=${lib.makeBinPath ([ cfg.wallpaperDaemon pkgs.procps ]
            ++ lib.optional cfg.enableLoops pkgs.mpvpaper
            ++ cfg.extraPath)}"
        ];
        # The display path must never hold the session up.
        TimeoutStartSec = "30s";
      };
    };

    systemd.user.timers.mcwall = {
      Unit.Description = "Re-select the Minecraft sky wallpaper";
      Timer = {
        OnStartupSec = "30s";
        OnUnitActiveSec = cfg.interval;
        # Catch up after a suspend or a late login instead of waiting out a
        # whole interval with the wrong sky on screen.
        Persistent = true;
        AccuracySec = "30s";
      };
      Install.WantedBy = [ "timers.target" ];
    };
  };
}
