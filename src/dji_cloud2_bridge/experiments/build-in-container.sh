#!/bin/sh
# Experimental build only. Caller must mount verified sources and external output.
set -eu
unset LD_PRELOAD LD_LIBRARY_PATH LD_AUDIT JAVA_TOOL_OPTIONS _JAVA_OPTIONS JDK_JAVA_OPTIONS
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY all_proxy no_proxy NO_PROXY
PATH=/opt/java/openjdk/bin:/usr/sbin:/usr/bin:/sbin:/bin
export PATH
[ "$(uname -m)" = aarch64 ] || { echo 'Only Linux aarch64 experiment has been validated.' >&2; exit 1; }
[ -f /work/sdk/VERSION ] && [ "$(cat /work/sdk/VERSION)" = 3.4.0 ] || exit 1
[ -f /work/lws/lib/client/client.c ] || exit 1

# Source archive was verified by the host before extraction. Patches apply once,
# and an unexpected upstream source tree fails instead of being built as-is.
patch -d /work -p1 --batch --forward < /patches/dji-sdk-3.4-experiment.patch
patch -d /work/lws -p1 --batch --forward < /patches/libwebsockets-2.4.2-experiment.patch
cmake -S /work/sdk -B /work/sdk-build --preset linux-aarch64-release \
  -DFH_SDK_DEBUG_SYMBOLS=OFF -DCMAKE_CXX_FLAGS_RELEASE='-O1 -DNDEBUG'
cmake --build /work/sdk-build --parallel "${CLOUD2_BUILD_JOBS:-6}"

cmake -S /work/lws -B /work/lws-build \
  -DLWS_OPENSSL_LIBRARIES='/work/sdk/third_party/openssl_1_1_1/lib/linux/aarch64/libssl.so.1.1;/work/sdk/third_party/openssl_1_1_1/lib/linux/aarch64/libcrypto.so.1.1' \
  -DLWS_OPENSSL_INCLUDE_DIRS='/work/sdk/third_party/openssl_1_1_1/include/linux/aarch64;/work/sdk/third_party/openssl_1_1_1/include' \
  -DOPENSSL_ROOT_DIR=/work/sdk/third_party/openssl_1_1_1 -DCMAKE_BUILD_TYPE=Release \
  -DLWS_WITH_STATIC=ON -DLWS_WITH_SHARED=ON -DLWS_WITH_SSL=ON \
  -DCMAKE_POSITION_INDEPENDENT_CODE=ON -DCMAKE_SKIP_RPATH=TRUE -DCMAKE_SKIP_INSTALL_RPATH=TRUE
cmake --build /work/lws-build --parallel "${CLOUD2_BUILD_JOBS:-6}"

mkdir -p /work/classes /work/jar-root/lib/linux-aarch64 /work/jar-root/THIRD_PARTY_LICENSES
find /work/sdk/platform/java/flight_hub_sdk/src/main/java -name '*.java' > /work/java-sources.txt
javac -J-Xmx1024m --release 17 -d /work/classes @/work/java-sources.txt
cp -a /work/classes/. /work/jar-root/
cp -a /work/sdk/platform/java/lib/linux-aarch64/. /work/jar-root/lib/linux-aarch64/
cp -a /work/sdk/platform/java/THIRD_PARTY_LICENSES/. /work/jar-root/THIRD_PARTY_LICENSES/
cp /work/sdk/LICENSE /work/jar-root/DJI-SDK-LICENSE
cp /work/sdk/NOTICE /work/jar-root/DJI-SDK-NOTICE
cp /work/lws/LICENSE /work/jar-root/LIBWEBSOCKETS-LICENSE
cp /work/sdk-build/libflighthubsdk_jni.so /work/jar-root/lib/linux-aarch64/
cp /work/lws-build/lib/libwebsockets.so.12 /work/jar-root/lib/linux-aarch64/
jar cf /output/sdk-patched.jar -C /work/jar-root .
sha256sum /work/jar-root/lib/linux-aarch64/libflighthubsdk_jni.so /work/jar-root/lib/linux-aarch64/libwebsockets.so.12 > /output/patched-native-sha256.txt

# Negative test control: same source/patches and runtime, except the two known
# redirect paths deliberately use vulnerable settings. NEVER load in production.
cp /work/sdk/platform/java/lib/linux-aarch64/libwebsockets.so.12 /work/jar-root/lib/linux-aarch64/libwebsockets.so.12
sed -i 's/CURLOPT_FOLLOWLOCATION, 0L/CURLOPT_FOLLOWLOCATION, 1L/' /work/sdk/deps/base/common/http_client/http_request_sync.cc
grep -q 'CURLOPT_FOLLOWLOCATION, 1L' /work/sdk/deps/base/common/http_client/http_request_sync.cc
cmake --build /work/sdk-build --parallel "${CLOUD2_BUILD_JOBS:-6}"
cp /work/sdk-build/libflighthubsdk_jni.so /work/jar-root/lib/linux-aarch64/libflighthubsdk_jni.so
jar cf /output/sdk-vulnerable-control.jar -C /work/jar-root .
sha256sum /output/sdk-patched.jar /output/sdk-vulnerable-control.jar > /output/jar-sha256.txt
cp /opt/cloud2-build-package-manifest.txt /output/build-packages.txt
