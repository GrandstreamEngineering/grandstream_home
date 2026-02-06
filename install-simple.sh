#!/bin/bash
set -e

# Simple Installation Script for Grandstream Home Integration
# A lightweight alternative to the full install.sh script

# Check the number of input parameters
if [ $# -ne 1 ]; then
    echo "usage: $0 [config_path]"
    echo "example: $0 /config"
    echo "         $0 ~/.homeassistant"
    exit 1
fi

# Get the config path
config_path=$1

# Check if config path exists
if [ ! -d "$config_path" ]; then
    echo "Error: $config_path does not exist"
    exit 1
fi

# Check if configuration.yaml exists
if [ ! -f "$config_path/configuration.yaml" ]; then
    echo "Warning: configuration.yaml not found in $config_path"
    echo "Please ensure this is a valid Home Assistant configuration directory"
fi

# Get the script path
script_path=$(dirname "$0")

# Set source and target
component_name=grandstream_home
source_path="$script_path/custom_components/$component_name"
target_root="$config_path/custom_components"
target_path="$target_root/$component_name"

# Check if source exists
if [ ! -d "$source_path" ]; then
    echo "Error: Source directory $source_path not found"
    exit 1
fi

# Remove the old version
if [ -d "$target_path" ]; then
    echo "Removing old version..."
    rm -rf "$target_path"
fi

# Copy the new version
echo "Installing Grandstream Home integration..."
mkdir -p "$target_root"
cp -r "$source_path" "$target_path"

# Set permissions
chmod -R 755 "$target_path"

# Done
echo "Grandstream Home installation completed successfully!"
echo "Please restart Home Assistant to complete the installation."
exit 0
