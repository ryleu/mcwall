#!/usr/bin/env bash
# Build the Fabric capture mod and install it into the render instance.
#
# Loom needs a JVM newer than the one the mod targets: it will not resolve on
# anything below 25, while the mod itself compiles to 21 because that is what
# the game runs. So Gradle gets a JDK 25 and `options.release = 21` handles
# the rest.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$here/mod"

export GRADLE_USER_HOME="${GRADLE_USER_HOME:-$here/var/gradle}"

# The devShell and the flake app both export MCWALL_LOOM_JDK. Outside either,
# fall back to asking nix for one.
if [ -n "${MCWALL_LOOM_JDK:-}" ]; then
    export JAVA_HOME="$MCWALL_LOOM_JDK"
elif [ -z "${JAVA_HOME:-}" ] || ! "$JAVA_HOME/bin/java" -version 2>&1 | grep -qE '"(2[5-9]|[3-9][0-9])'; then
    JAVA_HOME="$(nix build --no-link --print-out-paths nixpkgs#jdk25 | tail -1)"
    export JAVA_HOME
fi

echo "building with JAVA_HOME=$JAVA_HOME"
./gradlew build --console=plain "$@"

jar="$(find build/libs -name 'mcwall-capture-*.jar' ! -name '*sources*' | head -1)"
echo
echo "built $jar"
echo "run \`mcwall setup\` to install it into the render instance"
