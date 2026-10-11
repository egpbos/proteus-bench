#!/bin/sh
# Apply the bundled PROTEUS patches to every first-parent commit on main from
# #678 (1edab021) on, in a scratch index, and check that each patched source
# compiles and has the timing lines. Commits that log them already are skipped. Usage: check-proteus-patches.sh PROTEUS_CLONE [FIRST]
set -eu
clone=$1
first=${2:-1edab021}
patches=$(cd "$(dirname "$0")/../src/proteus_bench/proteus_patches" && pwd)
index=$(mktemp)
trap 'rm -f "$index"' EXIT
bad=0
for c in $(git -C "$clone" rev-list --first-parent --reverse "$first^..origin/main"); do
    # From FormingWorlds/PROTEUS#983 on, PROTEUS logs the lines itself
    git -C "$clone" show "$c:src/proteus/proteus.py" | grep -q INIT_TIMING && continue
    GIT_INDEX_FILE=$index git -C "$clone" read-tree "$c"
    ok=yes
    for p in "$patches"/*.patch; do
        GIT_INDEX_FILE=$index git -C "$clone" apply --cached -3 "$p" >/dev/null 2>&1 || ok=no
    done
    for f in src/proteus/proteus.py src/proteus/interior_energetics/aragog.py; do
        GIT_INDEX_FILE=$index git -C "$clone" show ":$f" |
            python3 -c "import sys; compile(sys.stdin.read(), '$f', 'exec')" 2>/dev/null || ok=no
    done
    GIT_INDEX_FILE=$index git -C "$clone" show :src/proteus/proteus.py | grep -q INIT_TIMING || ok=no
    if [ $ok = no ]; then
        echo "FAIL $(git -C "$clone" log -1 --format='%h %s' "$c")"
        bad=$((bad + 1))
    fi
done
echo "$bad commits failed"
[ $bad = 0 ]
