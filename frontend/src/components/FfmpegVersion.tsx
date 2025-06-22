import { useState, useEffect } from 'react';
import { Video, AlertCircle } from 'lucide-react';
import { getFfmpegVersion, FfmpegVersionResponse } from '../utils/api';

const FfmpegVersion = () => {
    const [ffmpegInfo, setFfmpegInfo] = useState<FfmpegVersionResponse | null>(null);
    const [isLoading, setIsLoading] = useState(true);

    useEffect(() => {
        const fetchFfmpegVersion = async () => {
            try {
                const info = await getFfmpegVersion();
                setFfmpegInfo(info);
            } catch (error) {
                console.error('Error fetching ffmpeg version:', error);
                setFfmpegInfo({ success: false, error: 'Failed to fetch ffmpeg version' });
            } finally {
                setIsLoading(false);
            }
        };

        fetchFfmpegVersion();
        // Refresh every 5 minutes
        const interval = setInterval(fetchFfmpegVersion, 300000);

        return () => clearInterval(interval);
    }, []);

    if (isLoading) {
        return (
            <div className="fixed bottom-4 left-4 flex items-center gap-2 px-3 py-2 bg-gray-100 dark:bg-gray-800 rounded-lg shadow-lg max-w-xs">
                <div className="w-4 h-4 border-2 border-t-blue-500 rounded-full animate-spin" />
                <span className="text-sm text-gray-600 dark:text-gray-300">Checking ffmpeg...</span>
            </div>
        );
    }

    if (!ffmpegInfo) {
        return null;
    }

    return (
        <div className="fixed bottom-4 left-4 flex items-center gap-2 px-3 py-2 bg-gray-100 dark:bg-gray-800 rounded-lg shadow-lg max-w-xs">
            {ffmpegInfo.success ? (
                <>
                    <Video className="w-4 h-4 text-green-500" />
                    <div className="text-sm text-gray-600 dark:text-gray-300">
                        <div className="font-medium">FFmpeg Available</div>
                        <div className="text-xs opacity-75 truncate">
                            {ffmpegInfo.version?.split('\n')[0] || 'Version info available'}
                        </div>
                    </div>
                </>
            ) : (
                <>
                    <AlertCircle className="w-4 h-4 text-red-500" />
                    <div className="text-sm text-gray-600 dark:text-gray-300">
                        <div className="font-medium">FFmpeg Error</div>
                        <div className="text-xs opacity-75">
                            {ffmpegInfo.error || 'Unknown error'}
                        </div>
                    </div>
                </>
            )}
        </div>
    );
};

export default FfmpegVersion; 