// Helpers for the "compress to a target file size" (Discord-shareable) feature.
// The dropdown lets a user cap the size of an extra copy; we predict up-front
// whether that cap will still look decent, using only metadata the browser can
// read locally (duration + resolution) — no server round-trip.

export interface VideoMeta {
    durationSec: number;
    width: number;
    height: number;
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

// Browsers don't reliably expose frame rate, so assume 30fps for the estimate.
const ASSUMED_FPS = 30;

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
    const bpp = (videoKbps * 1000) / (meta.width * meta.height * ASSUMED_FPS);
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

/** Read duration + resolution from a File locally via a throwaway <video>. */
export function probeVideoMeta(file: File): Promise<VideoMeta | null> {
    return new Promise((resolve) => {
        const url = URL.createObjectURL(file);
        const video = document.createElement('video');
        video.preload = 'metadata';
        const done = (meta: VideoMeta | null) => {
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
