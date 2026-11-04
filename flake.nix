{
  description = "Minecraft-rendered sky wallpaper for Hyprland";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
  };

  outputs = { self, nixpkgs }:
    let
      system = "x86_64-linux";
      pkgs = nixpkgs.legacyPackages.${system};

      # Runtime deps of the display path and the orchestrator.
      pythonDeps = ps: with ps; [
        astral
        httpx
        pillow
        tomli-w
        nbtlib # reads the camera back out of a world's level.dat
      ];
      # Extra deps used only by tests and the tuning tools.
      pythonDevDeps = ps: with ps; [
        pytest
        numpy
        skyfield # cross-check only; the shipped sun provider is astral
      ];

      pythonEnv = pkgs.python3.withPackages (ps: pythonDeps ps ++ pythonDevDeps ps);

      # Only what the Python package and its tests need. Without this the
      # Gradle cache and the decompiled sources under var/ (gigabytes) would be
      # copied into the store on every build.
      pythonSrc = pkgs.lib.cleanSourceWith {
        src = ./.;
        name = "mcwall-source";
        filter = path: type:
          let relative = pkgs.lib.removePrefix (toString ./. + "/") (toString path);
              top = builtins.head (pkgs.lib.splitString "/" relative);
          in !(builtins.elem top [ "var" "mod" "result" ".git" ".direnv" ]);
      };

      mcwall = pkgs.python3Packages.buildPythonApplication {
        pname = "mcwall";
        version = "0.1.0";
        src = pythonSrc;
        format = "pyproject";
        nativeBuildInputs = [ pkgs.python3Packages.setuptools ];
        propagatedBuildInputs = pythonDeps pkgs.python3Packages;
        nativeCheckInputs = [ pkgs.python3Packages.pytest ];
        checkPhase = ''
          runHook preCheck
          PYTHONPATH=$PWD:$PYTHONPATH pytest -q tests
          runHook postCheck
        '';
        # Tools the orchestrator shells out to at render and display time.
        makeWrapperArgs = [
          "--prefix" "PATH" ":" (pkgs.lib.makeBinPath [ pkgs.ffmpeg pkgs.libwebp ])
        ];
      };
    in
    {
      packages.${system} = {
        inherit mcwall;
        default = mcwall;
      };

      devShells.${system}.default = pkgs.mkShell {
        name = "mcwall-dev";
        packages = with pkgs; [
          pythonEnv

          # Capture mod toolchain. Loom will not resolve on a JVM below 25,
          # while the mod itself compiles to 21 because that is what the game
          # runs; `options.release = 21` bridges the two.
          jdk21
          jdk25
          gradle

          # Headless rendering
          cage
          wlr-randr
          mesa-demos # glxinfo / eglinfo for the GPU check
          vulkan-tools

          # Media
          ffmpeg
          libwebp
          imagemagick

          # Display path
          swww # NOTE: current nixpkgs ships this as awww/awww-daemon
          mpvpaper

          # Misc
          jq
          curl
          unzip
          upower
        ];

        shellHook = ''
          export MCWALL_ROOT="$PWD"
          export JAVA_HOME="${pkgs.jdk21}"
          # Loom needs this; scripts/build_mod.sh picks it up.
          export MCWALL_LOOM_JDK="${pkgs.jdk25}"
          export PYTHONPATH="$PWD:''${PYTHONPATH:-}"
          export GRADLE_USER_HOME="$PWD/var/gradle"
          echo "mcwall devShell — python $(python3 --version | cut -d' ' -f2), jdk $(java -version 2>&1 | head -1 | cut -d'"' -f2)"
        '';
      };

      # Building the capture mod is wrapped by nix but not a pure derivation:
      # Fabric Loom downloads the game, its mappings and the Fabric libraries
      # during the build and decompiles them, none of which a sandbox allows.
      # This gives the toolchain and one command; it does not give a
      # reproducible, cacheable jar.
      apps.${system} =
        let
          buildMod = {
            type = "app";
            program = toString (pkgs.writeShellScript "mcwall-build-mod" ''
              export PATH=${pkgs.lib.makeBinPath [
                pkgs.gradle pkgs.jdk25 pkgs.git pkgs.coreutils pkgs.findutils pkgs.gnugrep
              ]}:$PATH
              export MCWALL_LOOM_JDK=${pkgs.jdk25}
              exec ${./scripts/build_mod.sh} "$@"
            '');
          };
        in
        {
          build-mod = buildMod;
          default = buildMod;
        };

      checks.${system} = {
        tests = mcwall;
      };

      # Passed `self` so the module's default package needs no wiring.
      homeManagerModules.default = import ./nix/home-manager.nix self;
      homeManagerModules.mcwall = import ./nix/home-manager.nix self;

      formatter.${system} = pkgs.nixpkgs-fmt;
    };
}
