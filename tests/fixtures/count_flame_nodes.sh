#!/bin/sh
# Reference counts for a folded-stacks file, computed without proteus_bench:
# replace each run of native frames (labels ending in "]") with J (a Julia
# library appears in the run) or N, then list the distinct stack prefixes.
# Prints: tree nodes excluding the root, native blocks, Julia blocks.
# Usage: sh tests/fixtures/count_flame_nodes.sh tests/fixtures/real-slice.folded
prefixes=$(perl -pe 's/ \d+$//; s{(^|;)((?:[^;]*\](?:;|$))+)}{my($s,$r)=($1,$2); $s.($r=~/(julia|jl)[^\]]*\]/i?"J":"N").($r=~/;$/?";":"")}ge' "$1" |
  awk -F';' '{p=""; for (i = 1; i <= NF; i++) {p = p ";" $i; print p}}' | sort -u)
echo "$prefixes" | wc -l
echo "$prefixes" | awk -F';' '{print $NF}' | grep -cx N
echo "$prefixes" | awk -F';' '{print $NF}' | grep -cx J
