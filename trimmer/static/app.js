/* ============================================================
   Titanic Trimmer – Frontend Application
   ============================================================ */

(function () {
    'use strict';

    // ---- State ----
    let videos = [];
    let currentVideo = null;
    let videoDuration = 0;
    let trimStart = 0;
    let trimEnd = 0;
    let pausedAtTrimEnd = false;
    let audioTracks = [];
    let previewAudio = null; // synced <audio> element when previewing a non-default track
    let previewTrackIndex = 0; // which audio track is currently being previewed
    let audioMuted = false; // user's mute preference, persisted across track switches

    // ---- DOM refs ----
    const $ = (sel) => document.querySelector(sel);
    const libraryView = $('#libraryView');
    const editorView = $('#editorView');
    const videoGrid = $('#videoGrid');
    const searchInput = $('#searchInput');
    const folderDropdown = $('#folderDropdown');
    const sortDropdown = $('#sortDropdown');
    const backBtn = $('#backToLibrary');
    const videoPlayer = $('#videoPlayer');
    const videoTitle = $('#videoTitle');
    const videoMeta = $('#videoMeta');
    const startSlider = $('#startSlider');
    const endSlider = $('#endSlider');
    const trimRegion = $('#trimRegion');
    const playhead = $('#playhead');
    const startTimeInput = $('#startTimeInput');
    const endTimeInput = $('#endTimeInput');
    const trimDuration = $('#trimDuration');
    const overwriteToggle = $('#overwriteToggle');
    const trimBtn = $('#trimBtn');
    const confirmModal = $('#confirmModal');
    const confirmCancel = $('#confirmCancel');
    const confirmProceed = $('#confirmProceed');
    const audioTrackRow = $('#audioTrackRow');
    const audioTrackDropdown = $('#audioTrackDropdown');
    const trackKeepSection = $('#trackKeepSection');
    const trackKeepList = $('#trackKeepList');

    // Custom video controls
    const playerContainer = $('#playerContainer');
    const playPauseBtn = $('#playPauseBtn');
    const muteBtn = $('#muteBtn');
    const volumeSlider = $('#volumeSlider');
    const fullscreenBtn = $('#fullscreenBtn');
    const progressBar = $('#progressBar');
    const progressPlayed = $('#progressPlayed');
    const progressBuffered = $('#progressBuffered');
    const progressHandle = $('#progressHandle');
    const timeDisplay = $('#timeDisplay');
    const progressTrimStart = $('#progressTrimStart');
    const progressTrimEnd = $('#progressTrimEnd');
    const progressTrimRegion = $('#progressTrimRegion');

    // ---- Custom dropdown helpers ----
    function getDropdownValue(dropdown) {
        const selected = dropdown.querySelector('.custom-select-option.selected');
        return selected ? selected.dataset.value : '';
    }

    function setDropdownValue(dropdown, value) {
        dropdown.querySelectorAll('.custom-select-option').forEach(opt => opt.classList.remove('selected'));
        const match = dropdown.querySelector(`.custom-select-option[data-value="${value}"]`);
        if (match) {
            match.classList.add('selected');
            dropdown.querySelector('.custom-select-value').textContent = match.textContent;
        }
    }

    function initDropdown(dropdown, onChange) {
        const trigger = dropdown.querySelector('.custom-select-trigger');
        trigger.addEventListener('click', (e) => {
            e.stopPropagation();
            // Close other dropdowns
            document.querySelectorAll('.custom-select.open').forEach(d => {
                if (d !== dropdown) d.classList.remove('open');
            });
            dropdown.classList.toggle('open');
        });

        dropdown.querySelector('.custom-select-menu').addEventListener('click', (e) => {
            const option = e.target.closest('.custom-select-option');
            if (!option) return;
            dropdown.querySelectorAll('.custom-select-option').forEach(o => o.classList.remove('selected'));
            option.classList.add('selected');
            dropdown.querySelector('.custom-select-value').textContent = option.textContent;
            dropdown.classList.remove('open');
            if (onChange) onChange(option.dataset.value);
        });
    }

    // Close dropdowns on outside click
    document.addEventListener('click', () => {
        document.querySelectorAll('.custom-select.open').forEach(d => d.classList.remove('open'));
    });

    // ---- Init ----
    init();

    async function init() {
        await loadVideos();
        bindEvents();
    }

    // ---- API ----
    async function loadVideos() {
        try {
            const sort = getDropdownValue(sortDropdown);
            const folder = getDropdownValue(folderDropdown);
            const params = new URLSearchParams();
            if (sort && sort !== 'modified') params.set('sort', sort);
            if (folder) params.set('folder', folder);
            const qs = params.toString() ? '?' + params.toString() : '';
            const res = await fetch('/api/videos' + qs);
            if (!res.ok) throw new Error('Failed to load videos');
            const data = await res.json();
            videos = data.videos || [];
            renderLibrary();
        } catch (err) {
            videoGrid.innerHTML = '';
            const emptyState = document.createElement('div');
            emptyState.className = 'empty-state';
            emptyState.innerHTML = '<div class="empty-state-icon">📭</div><p>Failed to load videos</p>';
            const detail = document.createElement('p');
            detail.style.cssText = 'font-size:0.8rem;margin-top:8px';
            detail.textContent = err.message || 'Unknown error';
            emptyState.appendChild(detail);
            videoGrid.appendChild(emptyState);
        }
    }

    async function fetchDuration(path) {
        const res = await fetch(`/api/duration?path=${encodeURIComponent(path)}`);
        if (!res.ok) throw new Error('Failed to get duration');
        const data = await res.json();
        return data.duration;
    }

    async function requestTrim(path, startTime, endTime, overwrite, tracks) {
        const body = { path, startTime, endTime, overwrite };
        if (tracks) body.tracks = tracks;
        const res = await fetch('/api/trim', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || 'Trim failed');
        return data;
    }

    async function fetchAudioTracks(path) {
        try {
            const res = await fetch(`/api/tracks?path=${encodeURIComponent(path)}`);
            if (!res.ok) return [];
            const data = await res.json();
            return data.tracks || [];
        } catch {
            return [];
        }
    }

    // ---- Rendering ----
    function renderLibrary() {
        const search = searchInput.value.toLowerCase();
        const folder = getDropdownValue(folderDropdown);

        const filtered = videos.filter((v) => {
            if (search && !v.name.toLowerCase().includes(search)) return false;
            if (folder && v.folder !== folder) return false;
            return true;
        });

        // Populate folder filter
        const folders = [...new Set(videos.map((v) => v.folder).filter(Boolean))].sort();
        const currentFolder = getDropdownValue(folderDropdown);
        const menu = folderDropdown.querySelector('.custom-select-menu');
        menu.innerHTML = '';
        const allOpt = document.createElement('li');
        allOpt.className = 'custom-select-option' + (currentFolder === '' ? ' selected' : '');
        allOpt.dataset.value = '';
        allOpt.textContent = 'All Folders';
        menu.appendChild(allOpt);
        folders.forEach((f) => {
            const opt = document.createElement('li');
            opt.className = 'custom-select-option' + (f === currentFolder ? ' selected' : '');
            opt.dataset.value = f;
            opt.textContent = f;
            menu.appendChild(opt);
        });

        if (filtered.length === 0) {
            videoGrid.innerHTML = `
                <div class="empty-state">
                    <div class="empty-state-icon">🎬</div>
                    <p>${videos.length === 0 ? 'No videos found in Clips/' : 'No matching videos'}</p>
                </div>`;
            return;
        }

        videoGrid.innerHTML = filtered
            .map(
                (v, i) => `
            <div class="video-card" data-index="${videos.indexOf(v)}" style="animation-delay: ${i * 0.04}s">
                <img
                    class="video-card-thumb"
                    src="/api/thumbnail?path=${encodeURIComponent(v.path)}"
                    alt="${escapeHtml(v.name)}"
                    loading="lazy"
                    onerror="this.outerHTML='<div class=\\'thumb-placeholder\\'>🎬</div>'"
                >
                <div class="video-card-info">
                    <div class="video-card-name" title="${escapeHtml(v.name)}">${escapeHtml(v.name)}</div>
                    <div class="video-card-meta">
                        <span>${formatSize(v.size)}</span>
                        <span>${formatDate(v.modified)}</span>
                    </div>
                    ${v.folder ? `<span class="folder-badge">${escapeHtml(v.folder)}</span>` : ''}
                </div>
            </div>`
            )
            .join('');

        // Click handlers
        videoGrid.querySelectorAll('.video-card').forEach((card) => {
            card.addEventListener('click', () => {
                const idx = parseInt(card.dataset.index, 10);
                openEditor(videos[idx]);
            });
        });
    }

    // ---- Editor ----
    async function openEditor(video) {
        currentVideo = video;
        libraryView.style.display = 'none';
        editorView.style.display = 'block';
        backBtn.style.display = '';

        videoTitle.textContent = video.name;
        videoPlayer.src = `/api/video?path=${encodeURIComponent(video.path)}`;
        videoPlayer.load();

        // Get duration
        try {
            videoDuration = await fetchDuration(video.path);
        } catch {
            // Fallback: wait for metadata
            videoDuration = 0;
        }

        // Set up once metadata loaded
        videoPlayer.onloadedmetadata = () => {
            if (!videoDuration || videoDuration <= 0) {
                videoDuration = videoPlayer.duration;
            }
            trimStart = 0;
            trimEnd = videoDuration;
            updateSliders();
            updateTimeInputs();
            updateTrimInfo();
        };

        videoMeta.textContent = `${formatSize(video.size)} · ${video.folder || 'Clips'}`;
        overwriteToggle.checked = false;

        clearPreviewAudio();
        audioTracks = await fetchAudioTracks(video.path);
        renderAudioTrackUi();
    }

    function closeEditor() {
        editorView.style.display = 'none';
        libraryView.style.display = '';
        backBtn.style.display = 'none';
        clearPreviewAudio();
        videoPlayer.pause();
        videoPlayer.src = '';
        currentVideo = null;
        // Reload in case new files were created
        loadVideos();
    }

    // ---- Audio tracks ----
    function trackShortLabel(track, i) {
        // "Default mix (system + mic, EBU R128)" -> "Default mix"
        if (track.title) return track.title.split('(')[0].trim();
        return `Track ${i + 1}`;
    }

    function renderAudioTrackUi() {
        const multi = audioTracks.length > 1;
        audioTrackRow.style.display = multi ? '' : 'none';
        trackKeepSection.style.display = multi ? '' : 'none';
        if (!multi) return;

        const menu = audioTrackDropdown.querySelector('.custom-select-menu');
        menu.innerHTML = '';
        audioTracks.forEach((t, i) => {
            const opt = document.createElement('li');
            opt.className = 'custom-select-option' + (i === 0 ? ' selected' : '');
            opt.dataset.value = String(i);
            opt.textContent = trackShortLabel(t, i);
            if (t.title) opt.title = t.title;
            menu.appendChild(opt);
        });
        audioTrackDropdown.querySelector('.custom-select-value').textContent =
            trackShortLabel(audioTracks[0], 0);

        trackKeepList.innerHTML = '';
        audioTracks.forEach((t, i) => {
            const label = document.createElement('label');
            label.className = 'track-keep-item';
            const cb = document.createElement('input');
            cb.type = 'checkbox';
            cb.checked = true;
            cb.dataset.track = String(i);
            const text = document.createElement('span');
            text.textContent = t.title || trackShortLabel(t, i);
            label.appendChild(cb);
            label.appendChild(text);
            trackKeepList.appendChild(label);
        });
    }

    // The element currently producing sound: the video itself (default track)
    // or the synced <audio> element (alternate track).
    function audioSink() {
        return previewAudio || videoPlayer;
    }

    function clearPreviewAudio() {
        if (previewAudio) {
            previewAudio.pause();
            previewAudio.removeAttribute('src');
            previewAudio = null;
        }
        previewTrackIndex = 0;
        videoPlayer.volume = parseFloat(volumeSlider.value);
        videoPlayer.muted = audioMuted;
        syncMuteIcon();
    }

    function setPreviewTrack(index) {
        if (index === previewTrackIndex) return; // already previewing this track
        clearPreviewAudio();
        if (index === 0 || !currentVideo) return;
        previewTrackIndex = index;

        previewAudio = new Audio(
            `/api/audio?path=${encodeURIComponent(currentVideo.path)}&track=${index}`
        );
        previewAudio.preload = 'auto';
        previewAudio.volume = parseFloat(volumeSlider.value);
        previewAudio.muted = audioMuted; // carry the user's mute onto the new sink
        // If extraction fails, fall back to the video's own audio instead of
        // leaving playback silent with the video muted.
        previewAudio.addEventListener('error', () => onPreviewAudioError(index));
        videoPlayer.muted = true; // alternate track plays via previewAudio, not the video

        // Seek to the video's position only once the audio can honor it; setting
        // currentTime on a not-yet-loaded element is dropped and would start at 0.
        const syncWhenReady = () => {
            if (previewTrackIndex !== index || !previewAudio) return;
            previewAudio.currentTime = videoPlayer.currentTime;
            if (!videoPlayer.paused) playPreview(index);
        };
        if (previewAudio.readyState >= 1) syncWhenReady();
        else previewAudio.addEventListener('loadedmetadata', syncWhenReady, { once: true });

        syncMuteIcon();
    }

    function playPreview(index) {
        if (!previewAudio) return;
        previewAudio.play().catch((e) => {
            // Ignore autoplay-policy rejections; surface real failures.
            if (e && e.name !== 'NotAllowedError' && e.name !== 'AbortError') {
                onPreviewAudioError(index);
            }
        });
    }

    function onPreviewAudioError(index) {
        // Guard against stale handlers firing after the user moved on.
        if (previewTrackIndex !== index) return;
        clearPreviewAudio();
        setDropdownValue(audioTrackDropdown, '0');
        showToast('Could not load that audio track; playing default mix', 'error');
    }

    // ---- Timeline / Sliders ----
    function updateSliders() {
        if (!videoDuration) return;
        const startPct = (trimStart / videoDuration) * 100;
        const endPct = (trimEnd / videoDuration) * 100;
        startSlider.value = startPct;
        endSlider.value = endPct;
        trimRegion.style.left = startPct + '%';
        trimRegion.style.right = (100 - endPct) + '%';
        updateProgressTrimMarkers(startPct, endPct);
    }

    function updateProgressTrimMarkers(startPct, endPct) {
        // Show markers only when the trim range is not the full video
        const isTrimmed = startPct > 0.05 || endPct < 99.95;
        const display = isTrimmed ? 'block' : 'none';
        progressTrimStart.style.display = display;
        progressTrimEnd.style.display = display;
        progressTrimRegion.style.display = display;
        progressTrimStart.style.left = startPct + '%';
        progressTrimEnd.style.left = endPct + '%';
        progressTrimRegion.style.left = startPct + '%';
        progressTrimRegion.style.width = (endPct - startPct) + '%';
    }

    function updateTimeInputs() {
        startTimeInput.value = formatTime(trimStart);
        endTimeInput.value = formatTime(trimEnd);
        trimDuration.textContent = formatTime(trimEnd - trimStart);
    }

    function updateTrimInfo() {
        videoMeta.textContent = `${formatSize(currentVideo.size)} · ${currentVideo.folder || 'Clips'} · ${formatTime(videoDuration)} total`;
    }

    function updatePlayhead() {
        if (!videoDuration) return;
        const pct = (videoPlayer.currentTime / videoDuration) * 100;
        playhead.style.left = pct + '%';

        // Update custom progress bar
        progressPlayed.style.width = pct + '%';
        progressHandle.style.left = pct + '%';

        // Time display
        timeDisplay.textContent = `${formatTimeShort(videoPlayer.currentTime)} / ${formatTimeShort(videoDuration)}`;

        // Pause once at trim end point (user can resume playback past it)
        if (trimEnd < videoDuration && videoPlayer.currentTime >= trimEnd && !videoPlayer.paused && !pausedAtTrimEnd) {
            pausedAtTrimEnd = true;
            videoPlayer.pause();
        }

        // Correct alternate-track drift
        if (previewAudio && !videoPlayer.paused
            && Math.abs(previewAudio.currentTime - videoPlayer.currentTime) > 0.2) {
            previewAudio.currentTime = videoPlayer.currentTime;
        }
    }

    function updateBuffered() {
        if (!videoDuration || !videoPlayer.buffered.length) return;
        const buffEnd = videoPlayer.buffered.end(videoPlayer.buffered.length - 1);
        progressBuffered.style.width = (buffEnd / videoDuration) * 100 + '%';
    }

    function syncPlayPauseIcon() {
        const playIcon = playPauseBtn.querySelector('.play-icon');
        const pauseIcon = playPauseBtn.querySelector('.pause-icon');
        if (videoPlayer.paused) {
            playIcon.style.display = '';
            pauseIcon.style.display = 'none';
        } else {
            playIcon.style.display = 'none';
            pauseIcon.style.display = '';
        }
    }

    function syncMuteIcon() {
        const onIcon = muteBtn.querySelector('.vol-on-icon');
        const offIcon = muteBtn.querySelector('.vol-off-icon');
        const sink = audioSink();
        if (sink.muted || sink.volume === 0) {
            onIcon.style.display = 'none';
            offIcon.style.display = '';
        } else {
            onIcon.style.display = '';
            offIcon.style.display = 'none';
        }
    }

    function syncFullscreenIcon() {
        const enterIcon = fullscreenBtn.querySelector('.fs-enter-icon');
        const exitIcon = fullscreenBtn.querySelector('.fs-exit-icon');
        const isFs = !!(document.fullscreenElement || document.webkitFullscreenElement);
        enterIcon.style.display = isFs ? 'none' : '';
        exitIcon.style.display = isFs ? '' : 'none';
    }

    // Auto-hide controls
    let hideTimeout = null;
    function showControls() {
        playerContainer.classList.add('controls-visible');
        clearTimeout(hideTimeout);
        hideTimeout = setTimeout(() => {
            if (!videoPlayer.paused) {
                playerContainer.classList.remove('controls-visible');
            }
        }, 2500);
    }

    // ---- Event Binding ----
    function bindEvents() {
        backBtn.addEventListener('click', closeEditor);
        searchInput.addEventListener('input', renderLibrary);
        initDropdown(folderDropdown, () => loadVideos());
        initDropdown(sortDropdown, () => loadVideos());
        initDropdown(audioTrackDropdown, (v) => setPreviewTrack(parseInt(v, 10)));

        // ---- Custom video controls ----
        // Play/pause button
        playPauseBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            if (videoPlayer.paused) videoPlayer.play();
            else videoPlayer.pause();
        });

        // Click video to toggle play
        videoPlayer.addEventListener('click', () => {
            if (videoPlayer.paused) videoPlayer.play();
            else videoPlayer.pause();
        });

        videoPlayer.addEventListener('play', syncPlayPauseIcon);
        videoPlayer.addEventListener('pause', syncPlayPauseIcon);

        // Progress bar seek
        let isSeeking = false;

        function seekFromEvent(e) {
            const rect = progressBar.getBoundingClientRect();
            const pct = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
            const newTime = pct * videoDuration;
            // Re-arm trim-end pause if seeking before the end point
            if (newTime < trimEnd) pausedAtTrimEnd = false;
            videoPlayer.currentTime = newTime;
            updatePlayhead();
        }

        progressBar.parentElement.addEventListener('mousedown', (e) => {
            if (!videoDuration) return;
            isSeeking = true;
            seekFromEvent(e);
        });

        document.addEventListener('mousemove', (e) => {
            if (isSeeking) seekFromEvent(e);
        });

        document.addEventListener('mouseup', () => {
            if (!isSeeking) return;
            isSeeking = false;
            // Resync the alternate audio once at the drop point, not on every
            // intermediate seek during the drag.
            if (previewAudio) previewAudio.currentTime = videoPlayer.currentTime;
        });

        // Volume (applies to whichever element is producing sound)
        volumeSlider.addEventListener('input', () => {
            const sink = audioSink();
            sink.volume = parseFloat(volumeSlider.value);
            audioMuted = false;
            sink.muted = false;
            syncMuteIcon();
        });

        muteBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            audioMuted = !audioMuted;
            audioSink().muted = audioMuted;
            syncMuteIcon();
        });

        // Keep alternate audio track in lockstep with the video
        videoPlayer.addEventListener('play', () => {
            if (!previewAudio) return;
            previewAudio.currentTime = videoPlayer.currentTime;
            previewAudio.play().catch(() => {});
        });
        videoPlayer.addEventListener('pause', () => {
            if (previewAudio) previewAudio.pause();
        });
        videoPlayer.addEventListener('seeked', () => {
            // Skip during an active scrub-drag; mouseup does the single resync.
            if (previewAudio && !isSeeking) previewAudio.currentTime = videoPlayer.currentTime;
        });
        // The video freezes while buffering without firing 'pause', so hold the
        // audio during a stall and resync when playback actually resumes.
        videoPlayer.addEventListener('waiting', () => {
            if (previewAudio) previewAudio.pause();
        });
        videoPlayer.addEventListener('playing', () => {
            if (!previewAudio || videoPlayer.paused) return;
            previewAudio.currentTime = videoPlayer.currentTime;
            previewAudio.play().catch(() => {});
        });

        // Fullscreen
        fullscreenBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            if (document.fullscreenElement || document.webkitFullscreenElement) {
                (document.exitFullscreen || document.webkitExitFullscreen).call(document);
            } else {
                (playerContainer.requestFullscreen || playerContainer.webkitRequestFullscreen).call(playerContainer);
            }
        });

        document.addEventListener('fullscreenchange', syncFullscreenIcon);
        document.addEventListener('webkitfullscreenchange', syncFullscreenIcon);

        // Auto-hide
        playerContainer.addEventListener('mousemove', showControls);
        playerContainer.addEventListener('mouseleave', () => {
            clearTimeout(hideTimeout);
            if (!videoPlayer.paused) {
                playerContainer.classList.remove('controls-visible');
            }
        });

        // Playhead + buffer tracking
        videoPlayer.addEventListener('timeupdate', updatePlayhead);
        videoPlayer.addEventListener('progress', updateBuffered);

        // Trim slider events
        startSlider.addEventListener('input', () => {
            const pct = parseFloat(startSlider.value);
            trimStart = (pct / 100) * videoDuration;
            if (trimStart >= trimEnd - 0.1) {
                trimStart = trimEnd - 0.1;
                startSlider.value = (trimStart / videoDuration) * 100;
            }
            updateSliders();
            updateTimeInputs();
            videoPlayer.currentTime = trimStart;
        });

        endSlider.addEventListener('input', () => {
            const pct = parseFloat(endSlider.value);
            trimEnd = (pct / 100) * videoDuration;
            if (trimEnd <= trimStart + 0.1) {
                trimEnd = trimStart + 0.1;
                endSlider.value = (trimEnd / videoDuration) * 100;
            }
            pausedAtTrimEnd = false;
            updateSliders();
            updateTimeInputs();
            videoPlayer.currentTime = trimEnd;
        });

        // Time input events
        startTimeInput.addEventListener('change', () => {
            const t = parseTime(startTimeInput.value);
            if (t !== null && t >= 0 && t < trimEnd) {
                trimStart = t;
                updateSliders();
                updateTimeInputs();
                videoPlayer.currentTime = trimStart;
            } else {
                startTimeInput.value = formatTime(trimStart);
            }
        });

        endTimeInput.addEventListener('change', () => {
            const t = parseTime(endTimeInput.value);
            if (t !== null && t > trimStart && t <= videoDuration) {
                trimEnd = t;
                pausedAtTrimEnd = false;
                updateSliders();
                updateTimeInputs();
                videoPlayer.currentTime = trimEnd;
            } else {
                endTimeInput.value = formatTime(trimEnd);
            }
        });

        // Trim button
        trimBtn.addEventListener('click', handleTrimClick);

        // Modal
        confirmCancel.addEventListener('click', () => {
            confirmModal.style.display = 'none';
        });
        confirmProceed.addEventListener('click', () => {
            confirmModal.style.display = 'none';
            executeTrim(true);
        });

        // Keyboard shortcuts
        document.addEventListener('keydown', handleKeydown);
    }

    function handleKeydown(e) {
        // Don't trigger shortcuts when typing in inputs
        if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
        if (!currentVideo) return;

        switch (e.key) {
            case ' ':
                e.preventDefault();
                if (videoPlayer.paused) videoPlayer.play();
                else videoPlayer.pause();
                break;
            case 'i':
            case 'I':
                trimStart = videoPlayer.currentTime;
                if (trimStart >= trimEnd) trimStart = trimEnd - 0.1;
                updateSliders();
                updateTimeInputs();
                showToast('Start point set', 'info');
                break;
            case 'o':
            case 'O':
                trimEnd = videoPlayer.currentTime;
                if (trimEnd <= trimStart) trimEnd = trimStart + 0.1;
                pausedAtTrimEnd = false;
                updateSliders();
                updateTimeInputs();
                showToast('End point set', 'info');
                break;
            case 'ArrowLeft':
                e.preventDefault();
                videoPlayer.currentTime = Math.max(0, videoPlayer.currentTime - 5);
                if (videoPlayer.currentTime < trimEnd) pausedAtTrimEnd = false;
                break;
            case 'ArrowRight':
                e.preventDefault();
                videoPlayer.currentTime = Math.min(videoDuration, videoPlayer.currentTime + 5);
                break;
        }
    }

    // ---- Trim Execution ----
    function handleTrimClick() {
        if (!currentVideo) return;

        // Validate
        if (trimStart >= trimEnd) {
            showToast('Invalid trim range', 'error');
            return;
        }

        // If overwrite is checked, show confirmation
        if (overwriteToggle.checked) {
            confirmModal.style.display = '';
            return;
        }

        executeTrim(false);
    }

    function selectedTracks() {
        // null = keep everything (server default); array = explicit selection
        if (audioTracks.length <= 1) return null;
        const checked = [...trackKeepList.querySelectorAll('input:checked')]
            .map((cb) => parseInt(cb.dataset.track, 10));
        return checked.length === audioTracks.length ? null : checked;
    }

    async function executeTrim(overwrite) {
        const tracks = selectedTracks();
        if (tracks && tracks.length === 0) {
            showToast('Select at least one audio track to keep', 'error');
            return;
        }

        trimBtn.disabled = true;
        trimBtn.innerHTML = '<span class="spinner" style="width:18px;height:18px;border-width:2px;margin:0"></span> Trimming...';

        try {
            const result = await requestTrim(
                currentVideo.path,
                trimStart,
                trimEnd,
                overwrite,
                tracks
            );
            showToast(result.message, 'success');
        } catch (err) {
            showToast(err.message, 'error');
        } finally {
            trimBtn.disabled = false;
            trimBtn.innerHTML = '<span class="btn-icon">✂️</span> Save Trimmed Video';
        }
    }

    // ---- Utilities ----
    function formatTime(seconds) {
        if (!seconds || seconds < 0) return '0:00.000';
        const mins = Math.floor(seconds / 60);
        const secs = seconds % 60;
        return `${mins}:${secs.toFixed(3).padStart(6, '0')}`;
    }

    function formatTimeShort(seconds) {
        if (!seconds || seconds < 0) return '0:00';
        const mins = Math.floor(seconds / 60);
        const secs = Math.floor(seconds % 60);
        return `${mins}:${secs.toString().padStart(2, '0')}`;
    }

    function parseTime(str) {
        // Accept formats: "1:23.456", "83.456", "1:23"
        str = str.trim();
        const match = str.match(/^(\d+):(\d+(?:\.\d+)?)$/);
        if (match) {
            return parseInt(match[1], 10) * 60 + parseFloat(match[2]);
        }
        const num = parseFloat(str);
        return isNaN(num) ? null : num;
    }

    function formatSize(bytes) {
        if (bytes < 1024) return bytes + ' B';
        if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
        if (bytes < 1024 * 1024 * 1024) return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
        return (bytes / (1024 * 1024 * 1024)).toFixed(2) + ' GB';
    }

    function formatDate(timestamp) {
        const d = new Date(timestamp * 1000);
        return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
    }

    function escapeHtml(str) {
        const div = document.createElement('div');
        div.textContent = str;
        return div.innerHTML;
    }

    function showToast(message, type = 'info') {
        const container = $('#toastContainer');
        const toast = document.createElement('div');
        toast.className = `toast toast-${type}`;
        toast.textContent = message;
        container.appendChild(toast);
        setTimeout(() => toast.remove(), 4000);
    }
})();
