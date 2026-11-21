#!/bin/sh

url=$1
destination=$2

download() {
    curl --fail --location --silent --show-error --remove-on-error \
        --proto '=https' --proto-redir '=https' \
        --connect-timeout 10 --speed-limit 1024 --speed-time 20 \
        --output "$destination" "$1"
}

# Dated package URLs can redirect to archive.org even when the mirror has the file.
case "$url" in
    https://umea.archive.pkgbuild.com/repos/*/*.pkg.tar.* | \
    https://archive.archlinux.org/repos/*/*.pkg.tar.*)
        filename=${url##*/}
        versioned_package=${filename%.pkg.tar.*}
        without_arch=${versioned_package%-*}
        without_release=${without_arch%-*}
        package=${without_release%-*}
        letter=${package%"${package#?}"}
        download "${url%%/repos/*}/packages/$letter/$package/$filename" && exit 0
        ;;
esac

download "$url"
