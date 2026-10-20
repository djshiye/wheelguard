#!/usr/bin/env bash
# Install wheelguard from the DNF repository (the same two commands as in the README).
set -euo pipefail
sudo dnf config-manager addrepo --from-repofile=https://djshiye.github.io/wheelguard/wheelguard.repo
sudo dnf install wheelguard
