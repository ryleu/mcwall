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

    daemonExecutable = mkOption {
      type = types.str;
      default = "awww-daemon";
      description = ''
        Daemon binary inside wallpaperDaemon. "awww-daemon" in current
        nixpkgs, "swww-daemon" before the rename.
      '';
    };

    manageDaemon = mkOption {
      type = types.bool;
      default = true;
      description = ''
        Run the wallpaper daemon as its own user service.

        It cannot be a child of the mcwall unit: that unit is a oneshot, and
        systemd's default KillMode=control-group reaps everything in the
        cgroup when it exits, so a daemon started by `mcwall apply` would die
        seconds later and every tick would start a fresh one.

        Set false if you already run swww or awww some other way, and point
        daemonUnit at that service instead.
      '';
    };

    daemonUnit = mkOption {
      type = types.str;
      default = "mcwall-wallpaper-daemon.service";
      description = "Unit that must be running before the wallpaper is set.";
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

    # The daemon holds the wallpaper surface, so it has to outlive each run of
    # the oneshot. Its own unit, in its own cgroup.
    systemd.user.services.mcwall-wallpaper-daemon = mkIf cfg.manageDaemon {
      Unit = {
        Description = "Wallpaper daemon for mcwall";
        After = [ "graphical-session.target" ];
        PartOf = [ "graphical-session.target" ];
      };
      Service = {
        Type = "simple";
        ExecStart = "${cfg.wallpaperDaemon}/bin/${cfg.daemonExecutable}";
        Restart = "on-failure";
        RestartSec = "2s";
      };
      Install.WantedBy = [ "graphical-session.target" ];
    };

    systemd.user.services.mcwall = {
      Unit = {
        Description = "Set the Minecraft sky wallpaper for the current moment";
        # Needs a compositor to talk to; never block the session on it.
        After = [ "graphical-session.target" cfg.daemonUnit ];
        PartOf = [ "graphical-session.target" ];
        # mcwall apply fails outright without a daemon rather than starting
        # one, so the dependency is a hard Requires, not a Wants.
        Requires = [ cfg.daemonUnit ];
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
