import { getAuth } from 'firebase/auth';

const API_BASE_URL = 'https://compress.ivan.boston';

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

export const uploadVideo = async (file: File, shouldCompress: boolean = true): Promise<UploadResponse> => {
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
        const response = await fetch(`${API_BASE_URL}/upload`, {
            method: 'POST',
            headers: {
                'Authorization': `Bearer ${idToken}`
            },
            body: formData,
        });

        if (!response.ok) {
            const errorData = await response.json();
            throw new Error(errorData.error || 'Upload failed');
        }

        return { success: true };
    } catch (error) {
        return {
            success: false,
            error: error instanceof Error ? error.message : 'Upload failed'
        };
    }
};