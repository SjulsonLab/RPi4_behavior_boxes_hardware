# Pi 5 / Trixie Provisioning

This directory contains the first-pass Ansible scaffold for the currently
supported provisioning target:

- Raspberry Pi 5
- Debian 13 / Trixie
- repo checkout at `/home/pi/RPi4_behavior_boxes_hardware`

## Files

- `pi5_trixie.yml`: main playbook
- `inventory.example.ini`: small inventory example

The package and verification source of truth lives in:

- [`environment/rpi5_trixie_manifest.json`](/home/matt/Documents/RPi4_behavior_boxes_hardware/environment/rpi5_trixie_manifest.json)
- [`environment/rpi5_trixie.py`](/home/matt/Documents/RPi4_behavior_boxes_hardware/environment/rpi5_trixie.py)
- [`environment/rpi5_trixie_verifier.py`](/home/matt/Documents/RPi4_behavior_boxes_hardware/environment/rpi5_trixie_verifier.py)

There is also a local shell bootstrap entry point for the same target:

- [`scripts/bootstrap_pi5_trixie.sh`](/home/matt/Documents/RPi4_behavior_boxes_hardware/scripts/bootstrap_pi5_trixie.sh)

## Usage

Runtime-only provisioning:

```bash
ansible-playbook -i deploy/ansible/inventory.example.ini deploy/ansible/pi5_trixie.yml
```

Provision runtime plus development/testing tools:

```bash
ansible-playbook \
  -i deploy/ansible/inventory.example.ini \
  deploy/ansible/pi5_trixie.yml \
  -e behavbox_include_dev=true
```

## Important Note

The verifier treats plotting as a hard requirement. On the currently tested
`main` branch, the plotting probe may still fail or time out even after package
installation. That is intentional: provisioning success should not hide a
broken plotting path.
