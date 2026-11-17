#!/bin/sh
# Execute inside a network-isolated Linux/JDK 17 container with the newly built
# experimental JNI jar. The generated private key and credentials are synthetic.
set -eu
[ "$#" = 2 ] || { echo 'usage: run-native-ws-tls.sh /absolute/repository /absolute/experimental-sdk.jar' >&2; exit 2; }
repo=$1
jar=$2
[ -f "$jar" ] && [ -f "$repo/src/dji_cloud2_bridge/DjiCloud2Bridge.java" ] || exit 2
work=$(mktemp -d /tmp/dji-ws-test.XXXXXXXX)
trap 'rm -r -- "$work"' EXIT HUP INT TERM
keytool -genkeypair -alias synthetic -keyalg RSA -keysize 2048 -validity 1 \
  -dname 'CN=127.0.0.1' -ext 'SAN=IP:127.0.0.1' -ext 'bc=ca:true' \
  -storetype PKCS12 -keystore "$work/server.p12" -storepass synthetic -keypass synthetic >/dev/null 2>&1
keytool -exportcert -rfc -alias synthetic -keystore "$work/server.p12" -storepass synthetic -file "$work/ca.pem" >/dev/null 2>&1
# Explicit test-CA injection affects only this ephemeral source copy.
source_file="$repo/src/dji_cloud2_bridge/DjiCloud2Bridge.java"
[ "$(grep -c 'cfg.setVerifySsl(true);' "$source_file")" = 1 ] || exit 1
sed "s|cfg.setVerifySsl(true);|cfg.setSslCaPath(\"$work/ca.pem\"); cfg.setVerifySsl(true);|" "$source_file" > "$work/DjiCloud2Bridge.java"
javac --release 17 -cp "$jar" -d "$work" "$work/DjiCloud2Bridge.java" "$repo/tests/dji_cloud2_bridge/NativeWebSocketTlsTest.java"
timeout 90s java -Dws.test.dir="$work" -cp "$work:$jar" NativeWebSocketTlsTest
