self:
{
  config,
  lib,
  pkgs,
  ...
}:

let
  cfg = config.services.mcwall;
  inherit (lib)
    mkEnableOption
    mkOption
    mkIf
    types
    ;

  hasAwwwModule = config.services ? awww;
  awwwEnabled = hasAwwwModule && config.services.awww.enable;

  daemonUnit =
    if cfg.daemonUnit != null then
      cfg.daemonUnit
    else if awwwEnabled then
      "awww.service"
    else
      null;

  # Our unit invokes the client binary, so it needs it on PATH. Take it from
  # the daemon module when that is what is providing it.
  daemonPackage =
    if cfg.daemonPackage != null then
      cfg.daemonPackage
    else if awwwEnabled then
      config.services.awww.package
    else
      pkgs.awww;
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

    daemonUnit = mkOption {
      type = types.nullOr types.str;
      default = null;
      example = "my-wallpaper.service";
      description = ''
        Systemd user unit that runs the wallpaper daemon. `mcwall.service`
        gets `Requires=` and `After=` on it.

        Left null, this is `awww.service` when `services.awww.enable` is set
        (which `services.swww.enable` is an alias for). With neither, the
        configuration fails to evaluate rather than installing a timer that
        would error every five minutes — set this to point at your own unit.

        It cannot be left to `mcwall apply` to start the daemon: that unit is
        a oneshot, and systemd's default KillMode=control-group reaps the
        whole cgroup when it exits, so the daemon would die seconds later and
        every tick would start a fresh one.
      '';
    };

    daemonPackage = mkOption {
      type = types.nullOr types.package;
      default = null;
      defaultText = lib.literalExpression "config.services.awww.package";
      description = ''
        Package providing the wallpaper client binary (`awww` or `swww`), put
        on the unit's PATH. Taken from `services.awww` when that is enabled.
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
    assertions = [
      {
        assertion = daemonUnit != null;
        message = ''
          services.mcwall needs a wallpaper daemon to talk to, and none was found.

          Either enable home-manager's daemon module:

              services.awww.enable = true;

          or point mcwall at a unit you run yourself:

              services.mcwall.daemonUnit = "my-wallpaper.service";
        '';
      }
    ];

    home.packages = [ cfg.package ] ++ lib.optional cfg.enableLoops pkgs.mpvpaper;

    systemd.user.services.mcwall = {
      Unit = {
        Description = "Set the Minecraft sky wallpaper for the current moment";
        # Needs a compositor to talk to; never block the session on it.
        After = [ "graphical-session.target" ] ++ lib.optional (daemonUnit != null) daemonUnit;
        PartOf = [ "graphical-session.target" ];
        ConditionPathExists = "%h/.config/mcwall/config.toml";
        # mcwall apply fails outright without a daemon rather than starting
        # one, so the dependency is a hard Requires, not a Wants.
        Requires = lib.optional (daemonUnit != null) daemonUnit;
      };
      Service = {
        Type = "oneshot";
        ExecStart = "${cfg.package}/bin/mcwall apply";
        Environment = [
          "PATH=${
            lib.makeBinPath (
              [
                daemonPackage
                pkgs.procps
              ]
              ++ lib.optional cfg.enableLoops pkgs.mpvpaper
              ++ cfg.extraPath
            )
          }"
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
