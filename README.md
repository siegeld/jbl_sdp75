# JBL SDP-75

A Home Assistant custom integration for the JBL SDP-75 surround processor (Trinnov-based). Communicates over TCP on port 44100.

## Installation

### HACS (Custom Repository)

1. In HACS, go to **Integrations** > **Custom Repositories**
2. Add this repository URL and select **Integration** as the category
3. Install **JBL SDP-75**
4. Restart Home Assistant

### Manual

Copy `custom_components/jbl_sdp75/` into your Home Assistant `custom_components/` directory and restart.

## Setup

1. Go to **Settings** > **Devices & Services** > **Add Integration** > **JBL SDP-75**
2. Enter the hostname or IP address of your SDP-75

## Features

- Power, volume, and mute control
- Source/profile selection with live discovery
- Hide unwanted sources via integration options (Configure)
