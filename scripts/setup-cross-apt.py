#!/usr/bin/env python3
import pathlib
import subprocess
import sys

def main():
    print("Configuring multi-arch apt sources for arm64 cross-compilation...")
    
    # 1. Add arm64 architecture
    try:
        subprocess.run(["dpkg", "--add-architecture", "arm64"], check=True)
    except subprocess.CalledProcessError as e:
        print(f"Error adding arm64 architecture: {e}", file=sys.stderr)
        sys.exit(1)
        
    # 2. Modify existing ubuntu.sources to restrict to amd64
    sources_path = pathlib.Path("/etc/apt/sources.list.d/ubuntu.sources")
    if sources_path.exists():
        print("Restricting existing ubuntu.sources to amd64...")
        content = sources_path.read_text()
        stanzas = content.split("\n\n")
        new_stanzas = []
        suites = set()
        for s in stanzas:
            if not s.strip():
                continue
            lines = s.strip().split("\n")
            if not any(l.strip().startswith("Architectures:") for l in lines):
                lines.append("Architectures: amd64")
            new_stanzas.append("\n".join(lines))
            for l in lines:
                if l.strip().startswith("Suites:"):
                    for suite in l.split(":", 1)[1].strip().split():
                        suites.add(suite)
        sources_path.write_text("\n\n".join(new_stanzas) + "\n")
        
        # 3. Create ports sources for arm64
        if suites:
            suites_str = " ".join(sorted(suites))
            print(f"Creating arm64-ports.sources with suites: {suites_str}...")
            ports_content = f"""Types: deb
URIs: http://ports.ubuntu.com/ubuntu-ports/
Suites: {suites_str}
Components: main restricted universe multiverse
Architectures: arm64
Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg
"""
            pathlib.Path("/etc/apt/sources.list.d/arm64-ports.sources").write_text(ports_content)
    else:
        print("Warning: /etc/apt/sources.list.d/ubuntu.sources not found. Skipping apt sources restriction.")

if __name__ == "__main__":
    main()
