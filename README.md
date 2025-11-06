# Titanic

Securely upload videos from a website to your own media server (with bells and whistles)

## Overview

Titanic was made to solve a personal problem: sharing media (videos) from a home server with a low tech person. The idea is to allow someone to intuitively upload videos to a private  media server without exposing anything to the network. Oh, and there's some nice sauce like h264 -> h265 conversion; plus some audio processing.

The frontend is React + Vite on Firebase; Backend is Quart + Hypercorn for http3 and request streaming; Media server component is Rust w/Axum + Tokio to reduce strain on my home server hardware, plus I was inspired after taking [Nathan Mull](https://nmmull.github.io/#Top3BUProfIMO)'s CS 392 M1: Rust, in Practice and in Theory @ BU. Shipped with Sentry so I can keep an eye on things.

## Philosophy

Due to the non-technical background of the user, I chose to take on more complexity and design a system from the ground up to reduce end user effort. That means no Syncthing or Seafile, VPNs, or extra installations*. Just a website and upload button. 

*_Other than Plex to serve the media because let's be real, nothing else comes even close._

Oh and also because I like it I have CD pipelines everywhere :)

### Getting started
Finally, to get started run ``docker compose -f docker-compose.dev.yml up --build -d`` from root and take a closer look [here](./DEVELOPER_README.md).
