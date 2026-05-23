# Local Yosys Install

This directory is reserved for a local OSS CAD Suite install. OSS CAD Suite
contains Yosys and related open-source digital design tools.

The downloaded toolchain is intentionally not committed to Git.

## Automatic Install

Run from the repository root:

```bash
python scripts/install_yosys.py
```

The Python wrapper detects the current operating system and calls the matching
platform script.

To reinstall from scratch:

```bash
python scripts/install_yosys.py --force
```

## Automatic Install From Main

The main program can also check/install Yosys before it starts reading user
requests:

```bash
python main.py --ensure-yosys -config config.example.yaml
```

To force a reinstall:

```bash
python main.py --ensure-yosys --force-yosys-install -config config.example.yaml
```

Installer logs are written to stderr so stdout can keep the contest response
format.

## Windows

Run from the repository root:

```powershell
.\scripts\install_yosys.ps1
```

The Windows script downloads the `windows-x64` OSS CAD Suite release asset. The
upstream release is usually a self-extracting `.exe`, not a `.zip`.

Then add it to the current PowerShell session:

```powershell
$env:PATH = "$PWD\third_party\yosys\oss-cad-suite\bin;$env:PATH"
```

## Linux

Run from the repository root:

```bash
bash scripts/install_yosys.sh
```

Then add it to the current shell session:

```bash
export PATH="$PWD/third_party/yosys/oss-cad-suite/bin:$PATH"
```

## Verify

```bash
yosys -V
```

The scripts download the latest release from:

```text
https://github.com/YosysHQ/oss-cad-suite-build/releases
```
