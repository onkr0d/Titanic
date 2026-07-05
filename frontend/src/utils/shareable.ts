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

export type QualityVerdict = 'good' | 'ok' | 'rough' | 'unknown';

// Must mirror the backend budget in build_shareable_copy: fixed AAC audio track
// plus a container-overhead safety margin.
const AUDIO_KBPS = 128;
const SIZE_MARGIN = 0.95;
// Browsers don't reliably expose frame rate, so assume 30fps for the estimate.
const ASSUMED_FPS = 30;

/** Video bitrate (kbps) the target leaves after audio + overhead. */
export function estimateVideoKbps(targetMb: number, durationSec: number): number {
    if (!durationSec || durationSec <= 0) return 0;
    const totalKbps = (targetMb * 8 * 1024) / durationSec;
    return Math.floor(totalKbps * SIZE_MARGIN) - AUDIO_KBPS;
}

/**
 * Predict how a size-capped HEVC copy will look, via bits-per-pixel-per-frame.
 * Thresholds are tuned for x265 (roughly half the bitrate of x264 for parity).
 */
export function predictQuality(targetMb: number, meta?: VideoMeta): QualityVerdict {
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
    unknown: { dot: 'bg-gray-400', text: 'Reading video…' },
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
