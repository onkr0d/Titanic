import { getAuth } from 'firebase/auth';
import axios from 'axios';

// if in dev, use the emulator
const API_BASE_URL = import.meta.env.DEV ? 'http://localhost:6969' : 'https://titanic.ivan.boston';

export interface UploadResponse {
    success: boolean;
    error?: string;
}

export interface HealthCheckResponse {
    status: string;
}

export interface DiskSpaceInfo {
    total: number;
    used: number;
    free: number;
}

export interface UploadProgress {
    progress: number;
    loaded: number;
    total: number;
}

// chat what's the overhead of sending 2 get requests at the same time on the same timer
// what if we combine them into one 🤑🤑🤑
// they pay me the big bucks for these kinds of opimizations 🔥

export const checkBackendHealth = async (): Promise<boolean> => {
    try {
        const auth = getAuth();
        const user = auth.currentUser;

        if (!user) {
            throw new Error('User not authenticated');
        }

        const idToken = await user.getIdToken();
        const response = await fetch(`${API_BASE_URL}/api/health`, {
            headers: {
                'Authorization': `Bearer ${idToken}`
            }
        });
        return response.ok;
    } catch (error) {
        return false;
    }
};

export const getDiskSpace = async (): Promise<DiskSpaceInfo | null> => {
    try {
        const auth = getAuth();
        const user = auth.currentUser;

        if (!user) {
            throw new Error('User not authenticated');
        }

        const idToken = await user.getIdToken();
        const response = await fetch(`${API_BASE_URL}/space`, {
            headers: {
                'Authorization': `Bearer ${idToken}`
            }
        });

        if (!response.ok) {
            throw new Error('Failed to fetch disk space information');
        }

        return await response.json();
    } catch (error) {
        console.error('Error fetching disk space:', error);
        return null;
    }
};

export const uploadVideo = async (
    file: File, 
    shouldCompress: boolean = true,
    onProgress?: (progress: UploadProgress) => void
): Promise<UploadResponse> => {
    const formData = new FormData();
    formData.append('file', file);
    formData.append('shouldCompress', shouldCompress.toString());

    try {
        const auth = getAuth();
        const user = auth.currentUser;

        if (!user) {
            throw new Error('User not authenticated');
        }

        const idToken = await user.getIdToken();
        const response = await axios.post(`${API_BASE_URL}/upload`, formData, {
            headers: {
                'Authorization': `Bearer ${idToken}`
            },
            onUploadProgress: (progressEvent) => {
                if (onProgress && progressEvent.total) {
                    onProgress({
                        progress: Math.round((progressEvent.loaded * 100) / progressEvent.total),
                        loaded: progressEvent.loaded,
                        total: progressEvent.total
                    });
                }
            }
        });

        return { success: true };
    } catch (error) {
        return {
            success: false,
            error: error instanceof Error ? error.message : 'Upload failed'
        };
    }
};