#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────
#  GenLauncher-LP Setup Script
#  Interactive installer for GenLauncher Linux Port
# ──────────────────────────────────────────────────────────────
set -euo pipefail

REPO_URL="https://github.com/NeleBiH/GenLauncher-LP.git"
INSTALL_DIR="$HOME/GenLauncher-LP"
VENV_DIR="$INSTALL_DIR/.venv"

# ── Colors ───────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
DIM='\033[2m'
NC='\033[0m'

# ── ASCII Art Banner ─────────────────────────────────────────
show_banner() {
    echo -e "${CYAN}"
    cat << 'BANNER'

    ╔═══════════════════════════════════════════════════════════╗
    ║                                                           ║
    ║    ██████╗ ███████╗███╗   ██╗                             ║
    ║   ██╔════╝ ██╔════╝████╗  ██║                             ║
    ║   ██║  ███╗█████╗  ██╔██╗ ██║                             ║
    ║   ██║   ██║██╔══╝  ██║╚██╗██║                             ║
    ║   ╚██████╔╝███████╗██║ ╚████║                             ║
    ║    ╚═════╝ ╚══════╝╚═╝  ╚═══╝                            ║
    ║   ██╗      █████╗ ██╗   ██╗███╗   ██╗ ██████╗██╗  ██╗    ║
    ║   ██║     ██╔══██╗██║   ██║████╗  ██║██╔════╝██║  ██║    ║
    ║   ██║     ███████║██║   ██║██╔██╗ ██║██║     ███████║    ║
    ║   ██║     ██╔══██║██║   ██║██║╚██╗██║██║     ██╔══██║    ║
    ║   ███████╗██║  ██║╚██████╔╝██║ ╚████║╚██████╗██║  ██║    ║
    ║   ╚══════╝╚═╝  ╚═╝ ╚═════╝ ╚═╝  ╚═══╝ ╚═════╝╚═╝  ╚═╝    ║
    ║                                                           ║
    ║          ─── L I N U X   P O R T ───                      ║
    ║                                                           ║
    ║   Mod Manager for C&C Generals / Zero Hour                ║
    ║   Native Linux app — launches game via Steam/Proton       ║
    ║                                                           ║
    ╚═══════════════════════════════════════════════════════════╝

BANNER
    echo -e "${NC}"
}

# ── Helpers ──────────────────────────────────────────────────
info()    { echo -e "${GREEN}[+]${NC} $1"; }
warn()    { echo -e "${YELLOW}[!]${NC} $1"; }
error()   { echo -e "${RED}[x]${NC} $1"; }
header()  { echo -e "\n${BOLD}${CYAN}── $1 ──${NC}\n"; }

# ── Distro detection ────────────────────────────────────────
# Sets DISTRO_FAMILY to: arch, debian, fedora, suse, or unknown
detect_distro() {
    DISTRO_FAMILY="unknown"
    if [ -f /etc/os-release ]; then
        . /etc/os-release
        case "$ID ${ID_LIKE:-}" in
            *arch*)   DISTRO_FAMILY="arch" ;;
            *debian*|*ubuntu*) DISTRO_FAMILY="debian" ;;
            *fedora*|*rhel*|*centos*) DISTRO_FAMILY="fedora" ;;
            *suse*)   DISTRO_FAMILY="suse" ;;
        esac
    fi
}

# ── Package installer ───────────────────────────────────────
# Installs packages using the system package manager.
# Usage: pkg_install pkg_arch pkg_debian pkg_fedora pkg_suse
pkg_install() {
    local pkg_arch="$1" pkg_deb="$2" pkg_fed="$3" pkg_suse="$4"
    case "$DISTRO_FAMILY" in
        arch)   sudo pacman -S --needed --noconfirm $pkg_arch ;;
        debian) sudo apt-get install -y $pkg_deb ;;
        fedora) sudo dnf install -y $pkg_fed ;;
        suse)   sudo zypper install -y $pkg_suse ;;
        *)
            error "Unknown distro. Install manually: $pkg_arch (Arch) / $pkg_deb (Debian/Ubuntu) / $pkg_fed (Fedora)"
            return 1
            ;;
    esac
}

# ── Find Python >= 3.12 ─────────────────────────────────────
# Sets PYTHON to the path of a suitable python binary, or empty string.
find_python() {
    PYTHON=""
    for candidate in python3.14 python3.13 python3.12 python3; do
        if command -v "$candidate" &>/dev/null; then
            if "$candidate" -c "import sys; exit(0 if sys.version_info >= (3,12) else 1)" 2>/dev/null; then
                PYTHON="$candidate"
                return
            fi
        fi
    done
}

# ── Ensure all dependencies ─────────────────────────────────
ensure_dependencies() {
    header "Checking dependencies"
    detect_distro
    info "Detected distro family: ${BOLD}$DISTRO_FAMILY${NC}"

    # ── git ──
    if command -v git &>/dev/null; then
        info "git: $(git --version)"
    else
        warn "git not found — installing..."
        pkg_install git git git git
        info "git installed"
    fi

    # ── Python >= 3.12 ──
    find_python
    if [ -z "$PYTHON" ]; then
        warn "Python >= 3.12 not found — installing..."
        case "$DISTRO_FAMILY" in
            arch)   sudo pacman -S --needed --noconfirm python ;;
            debian) sudo apt-get install -y python3.12 python3.12-venv python3.12-dev ;;
            fedora) sudo dnf install -y python3.12 python3.12-devel ;;
            suse)   sudo zypper install -y python312 python312-devel ;;
            *)      error "Install Python >= 3.12 manually."; return 1 ;;
        esac
        find_python
        if [ -z "$PYTHON" ]; then
            error "Python >= 3.12 still not found after install attempt."
            return 1
        fi
    fi
    info "Python: $PYTHON ($($PYTHON --version 2>&1))"

    # ── python3-venv (Debian/Ubuntu need it separately) ──
    if ! "$PYTHON" -m venv --help &>/dev/null 2>&1; then
        warn "Python venv module missing — installing..."
        if [ "$DISTRO_FAMILY" = "debian" ]; then
            local pyver
            pyver=$("$PYTHON" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
            sudo apt-get install -y "python${pyver}-venv"
        fi
    fi

    # ── pip (some minimal installs lack it) ──
    if ! "$PYTHON" -m pip --version &>/dev/null 2>&1; then
        warn "pip not found — installing..."
        pkg_install python-pip python3-pip python3-pip python3-pip
    fi

    # ── unrar (optional but recommended) ──
    if command -v unrar &>/dev/null; then
        info "unrar: found"
    else
        warn "unrar not found — installing (needed for .rar mod archives)..."
        case "$DISTRO_FAMILY" in
            arch)   sudo pacman -S --needed --noconfirm unrar ;;
            debian) sudo apt-get install -y unrar || sudo apt-get install -y unrar-free ;;
            fedora) sudo dnf install -y unrar 2>/dev/null || warn "unrar not in repos — install from rpmfusion" ;;
            suse)   sudo zypper install -y unrar 2>/dev/null || warn "unrar not in repos" ;;
            *)      warn "Install unrar manually for .rar support" ;;
        esac
    fi

    echo ""
    info "All dependencies ready"
}

# ── Menu Actions ─────────────────────────────────────────────

do_install() {
    header "Installing GenLauncher-LP"

    if [ -d "$INSTALL_DIR" ]; then
        warn "Directory $INSTALL_DIR already exists."
        read -rp "  Overwrite? [y/N] " ans
        if [[ ! "$ans" =~ ^[Yy] ]]; then
            info "Cancelled."
            return
        fi
        rm -rf "$INSTALL_DIR"
    fi

    ensure_dependencies || return 1

    info "Cloning repository..."
    git clone "$REPO_URL" "$INSTALL_DIR"

    info "Creating virtual environment..."
    "$PYTHON" -m venv "$VENV_DIR"

    info "Installing Python packages..."
    "$VENV_DIR/bin/pip" install --upgrade pip -q
    "$VENV_DIR/bin/pip" install -e "$INSTALL_DIR[gui,rar]" -q

    info "Creating desktop entry..."
    create_desktop_entry

    echo ""
    info "${GREEN}${BOLD}Installation complete!${NC}"
    info "Run with: ${CYAN}$INSTALL_DIR/run.sh${NC}"
    info "Or select option 4 from this menu."
}

do_update() {
    header "Updating GenLauncher-LP"

    if [ ! -d "$INSTALL_DIR/.git" ]; then
        error "GenLauncher-LP is not installed at $INSTALL_DIR"
        error "Run Install first."
        return
    fi

    local prev_dir="$PWD"
    cd "$INSTALL_DIR"

    info "Pulling latest changes..."
    git pull --ff-only origin main

    if [ ! -d "$VENV_DIR" ]; then
        warn "Virtual environment missing — recreating..."
        find_python
        if [ -z "$PYTHON" ]; then
            error "Python >= 3.12 not found. Run Install instead."
            cd "$prev_dir"
            return
        fi
        "$PYTHON" -m venv "$VENV_DIR"
        "$VENV_DIR/bin/pip" install --upgrade pip -q
    fi

    info "Updating Python packages..."
    "$VENV_DIR/bin/pip" install -e ".[gui,rar]" -q

    info "Updating desktop entry..."
    create_desktop_entry

    cd "$prev_dir"

    echo ""
    info "${GREEN}${BOLD}Update complete!${NC}"
}

do_uninstall() {
    header "Uninstalling GenLauncher-LP"

    if [ ! -d "$INSTALL_DIR" ]; then
        warn "GenLauncher-LP is not installed at $INSTALL_DIR"
        return
    fi

    echo -e "  ${RED}This will remove:${NC}"
    echo "    $INSTALL_DIR"
    echo "    ~/.local/share/applications/genlauncher-lp.desktop"
    echo ""
    echo -e "  ${DIM}Your mod data in ~/.local/share/genlauncher/ will be kept.${NC}"
    echo ""
    read -rp "  Are you sure? [y/N] " ans
    if [[ ! "$ans" =~ ^[Yy] ]]; then
        info "Cancelled."
        return
    fi

    rm -rf "$INSTALL_DIR"
    rm -f "$HOME/.local/share/applications/genlauncher-lp.desktop"

    echo ""
    info "${GREEN}Uninstalled.${NC}"
    info "Mod data remains in ~/.local/share/genlauncher/"
    info "To remove everything: rm -rf ~/.local/share/genlauncher ~/.config/genlauncher"
}

do_run() {
    if [ ! -f "$INSTALL_DIR/run.sh" ]; then
        error "GenLauncher-LP is not installed. Run Install first."
        return
    fi
    info "Launching GenLauncher-LP..."
    "$INSTALL_DIR/run.sh"
}

create_desktop_entry() {
    local desktop_dir="$HOME/.local/share/applications"
    local icon_path="$INSTALL_DIR/src/genlauncher/ui/images/app_icon_256.png"
    mkdir -p "$desktop_dir"
    cat > "$desktop_dir/genlauncher-lp.desktop" << EOF
[Desktop Entry]
Type=Application
Name=GenLauncher-LP
Comment=Mod Manager for C&C Generals / Zero Hour (Linux)
Exec=$INSTALL_DIR/run.sh
Icon=$icon_path
Terminal=false
Categories=Game;
Keywords=cnc;generals;zerohour;mod;launcher;
EOF
    if command -v update-desktop-database &>/dev/null; then
        update-desktop-database "$desktop_dir" 2>/dev/null || true
    fi
    info "Desktop entry created"
}

# ── Main Menu ────────────────────────────────────────────────

show_menu() {
    echo -e "  ${BOLD}Select an option:${NC}\n"
    echo -e "    ${CYAN}1${NC})  Install"
    echo -e "    ${CYAN}2${NC})  Update"
    echo -e "    ${CYAN}3${NC})  Uninstall"
    echo -e "    ${CYAN}4${NC})  Run GenLauncher-LP"
    echo -e "    ${CYAN}5${NC})  Exit"
    echo ""
}

main() {
    show_banner

    while true; do
        show_menu
        read -rp "  Enter choice [1-5]: " choice
        echo ""
        case "$choice" in
            1) do_install ;;
            2) do_update ;;
            3) do_uninstall ;;
            4) do_run ;;
            5) echo -e "  ${DIM}See you on the battlefield, General.${NC}"; exit 0 ;;
            *) warn "Invalid choice. Enter 1-5." ;;
        esac
        echo ""
    done
}

main "$@"
