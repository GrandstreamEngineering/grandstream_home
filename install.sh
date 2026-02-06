#!/bin/bash

# Grandstream Home Integration Installer
# Installation script for Home Assistant custom integration

set -e

# Color definitions
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Print colored messages
print_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Check if running as root
check_root() {
    if [ "$EUID" -eq 0 ]; then
        print_error "Please do not run this script as root user"
        exit 1
    fi
}

# Detect Home Assistant configuration directory
detect_ha_config() {
    # Common Home Assistant configuration directory paths
    POSSIBLE_PATHS=(
        "$HOME/.homeassistant"
        "$HOME/homeassistant"
        "/config"
        "/config/homeassistant"
        "$HOME/ha_config"
        "/usr/share/hassio/homeassistant"
    )

    # If HA_CONFIG_DIR environment variable is specified, use it first
    if [ -n "$HA_CONFIG_DIR" ] && [ -d "$HA_CONFIG_DIR" ]; then
        HA_CONFIG="$HA_CONFIG_DIR"
        print_info "Using configuration directory from environment variable: $HA_CONFIG"
        return 0
    fi

    # Auto-detect configuration directory
    for path in "${POSSIBLE_PATHS[@]}"; do
        if [ -d "$path" ] && [ -f "$path/configuration.yaml" ]; then
            HA_CONFIG="$path"
            print_info "Detected Home Assistant configuration directory: $HA_CONFIG"
            return 0
        fi
    done

    print_error "Unable to auto-detect Home Assistant configuration directory"
    print_warning "Please set HA_CONFIG_DIR environment variable to your Home Assistant configuration directory"
    exit 1
}

# Create custom components directory
create_custom_components() {
    local custom_dir="$HA_CONFIG/custom_components"

    if [ ! -d "$custom_dir" ]; then
        print_info "Creating custom components directory: $custom_dir"
        mkdir -p "$custom_dir"
    fi

    return 0
}

# Install integration files
install_integration() {
    local custom_dir="$HA_CONFIG/custom_components"
    local integration_dir="$custom_dir/grandstream_home"

    # Check if already installed
    if [ -d "$integration_dir" ]; then
        print_warning "Existing Grandstream Home integration detected, creating backup..."
        mv "$integration_dir" "${integration_dir}.backup.$(date +%Y%m%d_%H%M%S)"
    fi

    print_info "Installing Grandstream Home integration to: $integration_dir"
    cp -r "$(dirname "$0")/custom_components/grandstream_home" "$custom_dir/"

    # Set permissions
    chmod -R 755 "$integration_dir"

    print_info "Integration files installed successfully"
    return 0
}

# Verify installation
verify_installation() {
    local integration_dir="$HA_CONFIG/custom_components/grandstream_home"
    local manifest_file="$integration_dir/manifest.json"

    if [ ! -d "$integration_dir" ]; then
        print_error "Integration directory does not exist: $integration_dir"
        return 1
    fi

    if [ ! -f "$manifest_file" ]; then
        print_error "manifest.json file does not exist"
        return 1
    fi

    print_info "Installation verification successful"
    return 0
}

# Show post-installation instructions
show_post_install_info() {
    echo ""
    print_info "Grandstream Home integration installed successfully!"
    echo ""
    print_info "Next steps:"
    echo "1. Restart Home Assistant"
    echo "2. In Home Assistant, go to: Settings > Devices & Services > Integrations"
    echo "3. Click '+ Add Integration' and search for 'Grandstream Home'"
    echo "4. Follow the configuration wizard to complete setup"
    echo ""
    print_warning "Please ensure your Home Assistant version meets requirements"
    print_warning "Refer to README.md for detailed configuration"
    echo ""
}

# Uninstall function
uninstall_integration() {
    local integration_dir="$HA_CONFIG/custom_components/grandstream_home"

    if [ ! -d "$integration_dir" ]; then
        print_warning "Grandstream Home integration is not installed"
        return 0
    fi

    print_info "Uninstalling Grandstream Home integration..."
    rm -rf "$integration_dir"
    print_info "Uninstallation complete, please restart Home Assistant"
}

# Main function
main() {
    echo "Grandstream Home Integration Installer"
    echo "====================================="

    # Check if uninstall mode
    if [ "$1" = "--uninstall" ]; then
        check_root
        detect_ha_config
        uninstall_integration
        exit 0
    fi

    # Check if help mode
    if [ "$1" = "--help" ] || [ "$1" = "-h" ]; then
        echo "Usage: $0 [options]"
        echo ""
        echo "Options:"
        echo "  --help, -h      Show this help message"
        echo "  --uninstall     Uninstall Grandstream Home integration"
        echo ""
        echo "Environment Variables:"
        echo "  HA_CONFIG_DIR   Specify Home Assistant configuration directory path"
        echo ""
        exit 0
    fi

    # Execute installation process
    check_root
    detect_ha_config
    create_custom_components
    install_integration
    verify_installation
    show_post_install_info
}

# Run main function
main "$@"
