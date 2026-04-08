## 2026/04/08

Provisioning and desktop plotting verification were brought to a reproducible checkpoint for the Raspberry Pi 5 hardware setup. A Pi 5 / Trixie provisioning manifest, verifier, bootstrap script, and Ansible scaffold were added so the runtime and development dependencies can be installed in a consistent way. The plotting path was refactored so desktop plotting is checked separately from the DRM/headless media runtime: plotting dependency checks are now lightweight, desktop-session aware, and guarded by a subprocess probe with a timeout instead of happening at `BehavBox` import time.

The current hardware verification step passed on the Pi for the provisioning checks and the desktop plotting checks. Specifically, the provisioning-oriented tests passed, the repository verifier passed both with and without the desktop plotting requirement, and the visible desktop plotting smoke test opened successfully on hardware. One separate GPIO-related incompatibility was also exposed during broader `BehavBox.prepare_session()` testing on the Pi 5, but that is a different issue from the plotting-path work and was not treated as a blocker for this checkpoint.

Files to push to git to save this progress:

- `.gitignore`
- `box_runtime/behavior/behavbox.py`
- `box_runtime/behavior/plotting_support.py`
- `deploy/ansible/README.md`
- `deploy/ansible/inventory.example.ini`
- `deploy/ansible/pi5_trixie.yml`
- `docs/implementation.md`
- `docs/rpi_os_package_requirements.md`
- `environment/rpi5_trixie.py`
- `environment/rpi5_trixie_manifest.json`
- `environment/rpi5_trixie_verifier.py`
- `scripts/bootstrap_pi5_trixie.sh`
- `tests/test_behavbox_plotting.py`
- `tests/test_plotting_support.py`
- `tests/test_rpi5_trixie_provisioning.py`
