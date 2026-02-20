import { getAuth } from 'firebase/auth';
import axios from 'axios';
import { getToken } from "firebase/app-check";
import { appCheck } from '../App';

// if in dev, use the emulator
const API_BASE_URL = (import.meta.env.DEV ? 'http://localhost:6969' : 'https://compress.ivan.boston') + '/api';

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

export interface FoldersResponse {
    folders: string[];
}

export interface AppConfig {
    default_folder: string | null;
}

export interface UploadProgress {
    progress: number;
    loaded: number;
    total: number;
}

async function authHeaders(): Promise<Record<string, string>> {
    const user = getAuth().currentUser;
    if (!user) throw new Error('User not authenticated');
    const idToken = await user.getIdToken(/* forceRefresh */ false);
    const { token: appCheckToken } = await getToken(appCheck);
    return {
      Authorization: `Bearer ${idToken}`,
      'X-Firebase-AppCheck': appCheckToken,
    };
  }
  

// chat what's the overhead of sending 2 get requests at the same time on the same timer
// what if we combine them into one 🤑🤑🤑
// they pay me the big bucks for these kinds of opimizations 🔥

export const checkBackendHealth = async (): Promise<boolean> => {
    try {
        const headers = await authHeaders();
        const response = await fetch(`${API_BASE_URL}/health`, {
            headers
        });
        return response.ok;
    } catch (error) {
        return false;
    }
};

export const getDiskSpace = async (): Promise<DiskSpaceInfo | null> => {
    try {
        const headers = await authHeaders();
        const response = await fetch(`${API_BASE_URL}/space`, {
            headers
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

export const getFolders = async (): Promise<string[]> => {
    try {
        const headers = await authHeaders();
        const response = await fetch(`${API_BASE_URL}/folders`, {
            headers
        });

        if (!response.ok) {
            throw new Error('Failed to fetch folders');
        }

        const data: FoldersResponse = await response.json();
        return data.folders;
    } catch (error) {
        console.error('Error fetching folders:', error);
        return [];
    }
};

export const getAppConfig = async (): Promise<AppConfig | null> => {
    try {
        const headers = await authHeaders();
        const response = await fetch(`${API_BASE_URL}/config`, {
            headers
        });

        if (!response.ok) {
            throw new Error('Failed to fetch app config');
        }

        return await response.json();
    } catch (error) {
        console.error('Error fetching app config:', error);
        return null;
    }
};

export const uploadVideo = async (
    file: File,
    shouldCompress: boolean = true,
    folder?: string,
    onProgress?: (progress: UploadProgress) => void
): Promise<UploadResponse> => {
    const formData = new FormData();
    formData.append('file', file);
    formData.append('shouldCompress', shouldCompress.toString());
    if (folder) {
        formData.append('folder', folder);
    }

    try {
        const headers = await authHeaders();
        await axios.post(`${API_BASE_URL}/upload`, formData, {
            headers,
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