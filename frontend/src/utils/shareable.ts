// Helpers for the "compress to a target file size" (Discord-shareable) feature.
// The dropdown lets a user cap the size of an extra copy; we predict up-front
// whether that cap will still look decent, using only metadata the browser can
// read locally (duration + resolution) — no server round-trip.

export interface VideoMeta {
    durationSec: number;
    width: number;
    height: number;
    fps: number;
    // false when fps came from the fallback
    fpsMeasured: boolean;
}

export interface SizeTier {
    label: string;
    mb: number;
    note?: string;
}

// Discord's current upload ceilings by tier. Values are the shareable caps.
export const DISCORD_TIERS: SizeTier[] = [
    { label: '10 MB', mb: 10, note: 'Free' },
    { label: '50 MB', mb: 50, note: 'Nitro Basic' },
    { label: '500 MB', mb: 500, note: 'Nitro' },
];

export type QualityVerdict = 'good' | 'ok' | 'rough' | 'fits' | 'unknown';

export interface ShareableConfig {
    audio_kbps: number;
    size_margin: number;
    min_video_kbps: number;
    max_target_mb: number;
    // Backend capability flags; default false so an older backend gets the old UI.
    skip_if_under?: boolean;
    supports_only?: boolean;
}

// Encode budget for the prediction. Defaults mirror the backend's
// build_shareable_copy; overridden at runtime from /api/config.
const config: ShareableConfig = {
    audio_kbps: 128,
    size_margin: 0.95,
    min_video_kbps: 60,
    max_target_mb: 2000,
    skip_if_under: false,
    supports_only: false,
};

export function applyShareableConfig(cfg?: Partial<ShareableConfig> | null) {
    Object.assign(config, cfg ?? {});
}

export function getMaxTargetMb(): number {
    return config.max_target_mb;
}

export function supportsShareableOnly(): boolean {
    return !!config.supports_only;
}

// fallback if unsupported (mkv/webm). high on purpose, so we oversize not under.
export const FALLBACK_FPS = 60;

/** Video bitrate (kbps) the target leaves after audio + overhead, floored like the backend. */
export function estimateVideoKbps(targetMb: number, durationSec: number): number {
    if (!durationSec || durationSec <= 0) return 0;
    const totalKbps = (targetMb * 8 * 1024) / durationSec;
    const videoKbps = Math.floor(totalKbps * config.size_margin) - config.audio_kbps;
    return Math.max(videoKbps, config.min_video_kbps);
}

/**
 * Predict how a size-capped HEVC copy will look, via bits-per-pixel-per-frame.
 * Thresholds are tuned for x265 (roughly half the bitrate of x264 for parity).
 * A file already under the cap reports 'fits' — the backend skips the extra copy.
 */
export function predictQuality(targetMb: number, meta?: VideoMeta, fileSizeBytes?: number): QualityVerdict {
    if (config.skip_if_under && fileSizeBytes && fileSizeBytes <= targetMb * 1024 * 1024) return 'fits';
    if (!meta || !meta.durationSec || !meta.width || !meta.height) return 'unknown';
    const videoKbps = estimateVideoKbps(targetMb, meta.durationSec);
    if (videoKbps <= 0) return 'rough';
    const bpp = (videoKbps * 1000) / (meta.width * meta.height * meta.fps);
    if (bpp >= 0.04) return 'good';
    if (bpp >= 0.02) return 'ok';
    return 'rough';
}

export const VERDICT_COPY: Record<QualityVerdict, { dot: string; text: string }> = {
    good: { dot: 'bg-green-500', text: 'Should look great' },
    ok: { dot: 'bg-yellow-500', text: 'Watchable, some quality loss' },
    rough: { dot: 'bg-red-500', text: 'Will look rough — try a bigger size' },
    fits: { dot: 'bg-blue-500', text: 'Already under the cap — no extra copy will be made' },
    unknown: { dot: 'bg-gray-400', text: "Can't predict quality" },
};

const FPS_CHUNK_BYTES = 1 << 20;
// bounds a runaway loop, not the file size - appendBuffer seeks straight to moov
const FPS_MAX_READS = 32;

// what an iso-bmff file can start with. matroska walks the whole file otherwise.
const ISOBMFF_BOXES = new Set(['ftyp', 'moov', 'mdat', 'free', 'skip', 'wide', 'pnot', 'styp']);

async function isIsoBmff(file: File): Promise<boolean> {
    const head = await file.slice(0, 8).arrayBuffer();
    if (head.byteLength < 8) return false;
    return ISOBMFF_BOXES.has(String.fromCharCode(...new Uint8Array(head, 4, 4)));
}

/** Average frame rate from the sample table, or null. MP4/MOV only. */
export async function probeFps(file: File): Promise<number | null> {
    if (!(await isIsoBmff(file))) return null;

    let MP4Box: typeof import('mp4box');
    try {
        // lazy - keeps 42KB out of the initial bundle
        MP4Box = await import('mp4box');
    } catch {
        return null;
    }

    const iso = MP4Box.createFile(false);
    let info: import('mp4box').Movie | null = null;
    iso.onReady = (movie) => { info = movie; };
    iso.onError = () => { info = null; };

    try {
        let pos = 0;
        for (let reads = 0; reads < FPS_MAX_READS && pos < file.size && !info; reads++) {
            const slice = await file.slice(pos, pos + FPS_CHUNK_BYTES).arrayBuffer();
            if (slice.byteLength === 0) break;
            const next = iso.appendBuffer(MP4Box.MP4BoxBuffer.fromArrayBuffer(slice, pos));
            pos = next > pos ? next : pos + slice.byteLength;
        }
    } catch {
        return null;
    } finally {
        iso.flush();
    }

    const track = (info as import('mp4box').Movie | null)?.videoTracks?.[0];
    if (!track?.nb_samples || !track.timescale) return null;
    // samples_duration over duration - the latter can include edit lists
    const ticks = track.samples_duration || track.duration;
    if (!ticks) return null;

    const fps = track.nb_samples / (ticks / track.timescale);
    return Number.isFinite(fps) && fps >= 1 && fps <= 240 ? fps : null;
}

/** Read duration + resolution from a File locally via a throwaway <video>. */
function probeElementMeta(file: File): Promise<Omit<VideoMeta, 'fps' | 'fpsMeasured'> | null> {
    return new Promise((resolve) => {
        const url = URL.createObjectURL(file);
        const video = document.createElement('video');
        video.preload = 'metadata';
        const done = (meta: Omit<VideoMeta, 'fps' | 'fpsMeasured'> | null) => {
            URL.revokeObjectURL(url);
            resolve(meta);
        };
        video.onloadedmetadata = () => {
            // MediaRecorder WebMs / fragmented MP4s report Infinity here.
            if (!Number.isFinite(video.duration) || video.duration <= 0) {
                done(null);
                return;
            }
            done({
                durationSec: video.duration,
                width: video.videoWidth,
                height: video.videoHeight,
            });
        };
        video.onerror = () => done(null);
        video.src = url;
    });
}

/** Duration + resolution + frame rate, read locally. */
export async function probeVideoMeta(file: File): Promise<VideoMeta | null> {
    const [base, fps] = await Promise.all([probeElementMeta(file), probeFps(file)]);
    if (!base) return null;
    return { ...base, fps: fps ?? FALLBACK_FPS, fpsMeasured: fps !== null };
}
