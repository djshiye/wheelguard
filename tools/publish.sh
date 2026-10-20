#!/usr/bin/env bash
# Build the wheelguard RPM in a Fedora container and publish it to the DNF repository that lives on
# the gh-pages branch (served by GitHub Pages). No CI: everything runs locally with podman.
#
#   tools/publish.sh                 build from HEAD, commit to the local gh-pages worktree
#   tools/publish.sh --push          same, then push main and gh-pages
#   tools/publish.sh --allow-dirty   build the working tree (tracked + untracked, not ignored);
#                                    for testing only, cannot be combined with --push
set -euo pipefail

# Where the repository is served. Switch to https://raw.githubusercontent.com/djshiye/wheelguard/gh-pages
# if GitHub Pages cannot be used.
REPO_URL="https://djshiye.github.io/wheelguard"
IMAGE="registry.fedoraproject.org/fedora:44"
KEEP=3
COMMIT_NAME="Dino Klisura"
COMMIT_EMAIL="115914552+djshiye@users.noreply.github.com"

push=0
allow_dirty=0
for arg in "$@"; do
    case "$arg" in
        --push) push=1 ;;
        --allow-dirty) allow_dirty=1 ;;
        -h|--help) sed -n '2,8p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "usage: $0 [--push] [--allow-dirty]" >&2; exit 2 ;;
    esac
done
if [ "$push" -eq 1 ] && [ "$allow_dirty" -eq 1 ]; then
    echo "Refusing to push a build made with --allow-dirty." >&2
    exit 2
fi

root=$(cd "$(dirname "$0")/.." && pwd)
cd "$root"
wt="$root/.gh-pages"

if [ -t 1 ]; then b=$'\e[1m'; c=$'\e[36m'; r=$'\e[0m'; else b=""; c=""; r=""; fi
step() { printf '%s==>%s %s%s%s\n' "$c" "$r" "$b" "$*" "$r"; }

# ---- 1. version + release suffix ----
if [ "$allow_dirty" -eq 0 ] && [ -n "$(git status --porcelain)" ]; then
    echo "Working tree is dirty; commit or stash first (or use --allow-dirty to test)." >&2
    git status --short >&2
    exit 1
fi
version=$(awk '/^Version:/ {print $2; exit}' build-aux/wheelguard.spec)
release_suffix=.$(git rev-list --count HEAD)
[ "$allow_dirty" -eq 0 ] || release_suffix+=.dirty
fedora=${IMAGE##*:}
name=wheelguard-$version
step "Building $name (release suffix $release_suffix) for Fedora $fedora"

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
mkdir -p "$work/out"

# ---- 2. source tarball ----
if [ "$allow_dirty" -eq 0 ]; then
    git archive --format=tar.gz --prefix="$name/" -o "$work/$name.tar.gz" HEAD
else
    # tracked + untracked files that are not ignored, as they are on disk right now
    git ls-files -z --cached --others --exclude-standard | sort -z | while IFS= read -r -d '' f; do
        if [ -e "$f" ] || [ -L "$f" ]; then printf '%s\0' "$f"; fi
    done | tar --null -T - --transform "s,^,$name/," -czf "$work/$name.tar.gz"
fi

# ---- 3. gh-pages worktree ----
step "Preparing the gh-pages worktree (.gh-pages/)"
# Base on origin/gh-pages whenever it exists so the final push is a fast-forward.
if git fetch -q origin +gh-pages:refs/remotes/origin/gh-pages 2>/dev/null; then
    echo "   fetched origin/gh-pages"
fi
have_remote=0
git show-ref --verify --quiet refs/remotes/origin/gh-pages && have_remote=1
if ! git worktree list --porcelain | grep -qx "worktree $wt"; then
    if git show-ref --verify --quiet refs/heads/gh-pages; then
        git worktree add "$wt" gh-pages
    elif [ "$have_remote" -eq 1 ]; then
        git worktree add -b gh-pages "$wt" origin/gh-pages
    else
        git worktree add --orphan -b gh-pages "$wt"
    fi
fi
if [ "$have_remote" -eq 1 ]; then
    if ! git -C "$wt" merge -q --ff-only origin/gh-pages; then
        echo "Local gh-pages has diverged from origin/gh-pages; reconcile it in .gh-pages/ first." >&2
        exit 1
    fi
fi

# ---- 4. build, copy, prune and index inside the container ----
# Fedora version in the path, like the baseurl's $releasever.
repo_dir="$wt/fedora-$fedora"
mkdir -p "$repo_dir"
cache="${XDG_CACHE_HOME:-$HOME/.cache}/wheelguard-publish/dnf"
mkdir -p "$cache"

step "Running rpmbuild and createrepo_c in $IMAGE"
podman run --rm \
    -v "$work:/work:Z" -v "$repo_dir:/repo:Z" -v "$cache:/var/cache/libdnf5:Z" \
    -e NAME="$name" -e SUFFIX="$release_suffix" -e FEDORA="$fedora" -e KEEP="$KEEP" \
    "$IMAGE" bash -euo pipefail -c '
        dnf install -y --setopt=install_weak_deps=False \
            rpm-build createrepo_c python3-devel python3-evdev desktop-file-utils appstream \
            systemd-rpm-macros >/dev/null
        [ "$(rpm -E %fedora)" = "$FEDORA" ] || { echo "container is not Fedora $FEDORA" >&2; exit 1; }
        mkdir -p /root/rpmbuild/SOURCES
        cp /work/$NAME.tar.gz /root/rpmbuild/SOURCES/
        tar -xzOf /work/$NAME.tar.gz $NAME/build-aux/wheelguard.spec > /work/wheelguard.spec
        if ! rpmbuild -ba --define "release_suffix $SUFFIX" /work/wheelguard.spec >/work/rpmbuild.log 2>&1; then
            tail -n 60 /work/rpmbuild.log >&2
            exit 1
        fi
        grep -E "^(Wrote:|Checking for unpackaged)" /work/rpmbuild.log
        cp /root/rpmbuild/RPMS/noarch/*.rpm /root/rpmbuild/SRPMS/*.src.rpm /work/out/
        cp /work/out/*.rpm /repo/
        # keep the newest $KEEP binary and source packages
        for pat in "wheelguard-[0-9]*.noarch.rpm" "wheelguard-[0-9]*.src.rpm"; do
            ls /repo/$pat | sort -V | head -n -"$KEEP" | xargs -r rm -v
        done
        createrepo_c --update --general-compress-type gz /repo
    '

# ---- 5. repo file, landing page, commit ----
step "Writing wheelguard.repo and index.html"
cat > "$wt/wheelguard.repo" <<REPO
[wheelguard]
name=Wheelguard
baseurl=$REPO_URL/fedora-\$releasever/
enabled=1
# The packages are built locally and are not signed.
gpgcheck=0
metadata_expire=1h
REPO
touch "$wt/.nojekyll"
install -m 0644 build-aux/io.github.djshiye.Wheelguard.svg "$wt/icon.svg"
cat > "$wt/index.html" <<HTML
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Wheelguard DNF repository</title>
<link rel="icon" href="icon.svg">
<style>
  :root { color-scheme: dark; }
  body { margin: 0; min-height: 100vh; display: grid; place-items: center; background: #0c0d11;
         color: #e6e6ee; font: 16px/1.5 system-ui, sans-serif; }
  main { width: min(640px, 92vw); text-align: center; }
  img { width: 96px; height: 96px; }
  h1 { margin: .4em 0 .1em; font-size: 2rem;
       background: linear-gradient(90deg, #9b6bff, #22d3ee); -webkit-background-clip: text;
       background-clip: text; color: transparent; }
  p { margin: 0 0 1.4em; color: #9a9ab0; }
  pre { margin: 0; padding: 1em 1.2em; text-align: left; overflow-x: auto; background: #161720;
        border: 1px solid #262838; border-radius: 12px; font-size: .85rem; }
  a { color: #22d3ee; }
  small { display: block; margin-top: 1.6em; color: #6c6c82; }
</style>
</head>
<body>
<main>
  <img src="icon.svg" alt="">
  <h1>Wheelguard</h1>
  <p>DNF repository for Fedora</p>
  <pre>sudo dnf config-manager addrepo --from-repofile=$REPO_URL/wheelguard.repo
sudo dnf install wheelguard</pre>
  <small><a href="https://github.com/djshiye/wheelguard">github.com/djshiye/wheelguard</a>
  &middot; unsigned packages &middot; not affiliated with Endgame Gear</small>
</main>
</body>
</html>
HTML

step "Committing to gh-pages"
git -C "$wt" add -A
if git -C "$wt" diff --cached --quiet; then
    echo "   nothing changed"
else
    GIT_AUTHOR_NAME=$COMMIT_NAME GIT_AUTHOR_EMAIL=$COMMIT_EMAIL \
    GIT_COMMITTER_NAME=$COMMIT_NAME GIT_COMMITTER_EMAIL=$COMMIT_EMAIL \
        git -C "$wt" commit -q -m "Publish $name-1$release_suffix.fc$fedora"
    git -C "$wt" log --oneline -1
fi

# ---- 6. push (only on request): main first, then gh-pages ----
if [ "$push" -eq 1 ]; then
    step "Pushing main and gh-pages"
    git push origin main
    git -C "$wt" push origin gh-pages
else
    echo
    echo "Built locally, nothing pushed. To publish: tools/publish.sh --push"
fi
echo "Repository directory: $repo_dir"
