import { useState, useEffect } from 'react';
import { HardDrive } from 'lucide-react';
import { getDiskSpace, DiskSpaceInfo } from '../utils/api';
import Tooltip from './Tooltip';

const DiskSpaceIndicator = () => {
    const [diskSpace, setDiskSpace] = useState<DiskSpaceInfo | null>(null);
    const [isLoading, setIsLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    // Format bytes to human-readable format
    const formatBytes = (bytes: number): string => {
        if (bytes === 0) return '0 B';

        const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
        const i = Math.floor(Math.log(bytes) / Math.log(1024));

        return `${(bytes / Math.pow(1024, i)).toFixed(2)} ${sizes[i]}`;
    };

    useEffect(() => {
        const fetchDiskSpace = async () => {
            try {
                setIsLoading(true);
                const data = await getDiskSpace();
                setDiskSpace(data);
                setError(null);
            } catch (err) {
                setError('Failed to fetch disk space');
                console.error(err);
            } finally {
                setIsLoading(false);
            }
        };

        fetchDiskSpace();
        // Refresh every 30 seconds
        const interval = setInterval(fetchDiskSpace, 30000);

        return () => clearInterval(interval);
    }, []);

    // Calculate percentage of used space
    const usedPercentage = diskSpace ? (diskSpace.used / diskSpace.total) * 100 : 0;

    // Determine color based on usage
    const getColorClass = () => {
        if (usedPercentage > 90) return 'bg-red-500';
        if (usedPercentage > 70) return 'bg-yellow-500';
        return 'bg-green-500';
    };

    if (isLoading) {
        return (
            <div className="fixed bottom-4 left-4 flex items-center gap-2 px-3 py-2 bg-gray-100 dark:bg-gray-800 rounded-lg shadow-lg">
                <div className="w-4 h-4 border-2 border-t-blue-500 rounded-full animate-spin" />
                <span className="text-sm text-gray-600 dark:text-gray-300">Loading space info...</span>
            </div>
        );
    }

    if (error || !diskSpace) {
        return (
            <div className="fixed bottom-4 left-4 flex items-center gap-2 px-3 py-2 bg-gray-100 dark:bg-gray-800 rounded-lg shadow-lg">
                <HardDrive className="w-4 h-4 text-red-500" />
                <span className="text-sm text-gray-600 dark:text-gray-300">Space info unavailable</span>
            </div>
        );
    }

    return (
        <div className="fixed bottom-4 left-4 flex items-center gap-2 px-3 py-2 bg-gray-100 dark:bg-gray-800 rounded-lg shadow-lg">
            <HardDrive className="w-4 h-4 text-gray-600 dark:text-gray-300" />
            <div className="flex flex-col w-full">
                <div className="flex items-center gap-2">
                    <span className="text-sm font-medium text-gray-700 dark:text-gray-300">Disk Space</span>
                    <span className="text-xs text-gray-500 dark:text-gray-400">{formatBytes(diskSpace.used)} / {formatBytes(diskSpace.total)}</span>
                </div>
                <div className="w-full h-2 bg-gray-200 dark:bg-gray-700 rounded-full overflow-hidden">
                    <div
                        className={`h-full ${getColorClass()}`}
                        style={{ width: `${usedPercentage}%` }}
                    ></div>
                </div>
            </div>
        </div>
    );
};

export default DiskSpaceIndicator; 