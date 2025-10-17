# Titanic

Upload videos from a website to your own media server (with bells and whistles)

## Overview

Titanic was made to solve a personal problem: sharing media from a home server with a low tech person. The idea is to allow someone to intuitively upload videos to a private  media server without exposing anything to the network. Oh, and there's some nice sauce like h264 -> h265 conversion; plus some audio processing.

Frontend is React + Vite on Firebase; Backend is Quart + Hypercorn for http3 and streaming; Media server component is Rust to reduce strain on home server and I was inspired after taking [Nathan Mull](https://nmmull.github.io/#Top3BUProfIMO)'s CS 392 M1: Rust, in Practice and in Theory.

## Philosophy

Due to the non-technical background of the user, I chose to design a system from the ground up to reduce end user complexity. That means no Syncthing/Seafile, VPNs, or extra installations. Other than Plex to serve the videos and let's be real nothing comes even close.


Oh and also because I like it I have CD pipelines like everywhere :)

### Getting started
Finally, to get started run ``docker compose -f docker-compose.dev.yml up --build -d`` from root and take a closer look [here](./DEVELOPER_README.md).