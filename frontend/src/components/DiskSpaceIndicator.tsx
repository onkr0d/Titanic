import { useState, useEffect } from 'react';
import { Fish, ServerCrash } from 'lucide-react';
import { Skeleton } from 'boneyard-js/react';
import { getDiskSpace, DiskSpaceInfo } from '../utils/api';

// sizes the skeleton while loading
const PLACEHOLDER: DiskSpaceInfo = { total: 2e12, used: 1.1e12, free: 0.9e12 };

// Format bytes to human-readable format
const formatBytes = (bytes: number): string => {
    if (bytes === 0) return '0 B';

    const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
    const i = Math.floor(Math.log(bytes) / Math.log(1024));

    return `${(bytes / Math.pow(1024, i)).toFixed(2)} ${sizes[i]}`;
};

const DiskSpaceIndicator = () => {
    const [diskSpace, setDiskSpace] = useState<DiskSpaceInfo | null>(null);
    const [isLoading, setIsLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        const fetchDiskSpace = async () => {
            try {
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
        // Refresh every 30 seconds, but skip if tab is hidden (avoids Firebase timeout errors)
        const interval = setInterval(() => {
            if (!document.hidden) fetchDiskSpace();
        }, 30000);

        return () => clearInterval(interval);
    }, []);

    if (!error && (isLoading || diskSpace)) {
        return (
            <div className="flex items-center px-3 py-2 bg-gray-100 dark:bg-gray-800 rounded-lg shadow-lg w-fit md:fixed md:bottom-4 md:left-4">
                <Skeleton
                    name="disk-space"
                    loading={isLoading}
                    fixture={<DiskSpaceContent info={PLACEHOLDER} showFill={false} />}
                    color="#d1d5db"
                    darkColor="#374151"
                    transition
                >
                    <DiskSpaceContent info={diskSpace ?? PLACEHOLDER} />
                </Skeleton>
            </div>
        );
    }

    return (
        <div className="flex items-center gap-2 px-3 py-2 bg-gray-100 dark:bg-gray-800 rounded-lg shadow-lg w-fit md:fixed md:bottom-4 md:left-4">
            <ServerCrash className="w-4 h-4 text-red-500" />
            <span className="text-sm text-gray-600 dark:text-gray-300">Space info unavailable</span>
        </div>
    );
};

// showFill=false leaves the track childless so boneyard captures it as a full-width bone
const DiskSpaceContent = ({ info, showFill = true }: { info: DiskSpaceInfo; showFill?: boolean }) => {
    const usedPercentage = (info.used / info.total) * 100;
    const colorClass = usedPercentage > 80 ? 'bg-red-500' : usedPercentage > 50 ? 'bg-yellow-500' : 'bg-green-500';

    return (
        <div className="flex items-center gap-2">
            <Fish className="w-4 h-4 text-gray-600 dark:text-gray-300" />
            <div className="flex flex-col gap-1.5 w-full">
                <div className="flex items-baseline gap-2">
                    <span className="text-sm leading-none font-medium text-gray-700 dark:text-gray-300">Disk space</span>
                    <span className="text-xs leading-none text-gray-500 dark:text-gray-400">{formatBytes(info.used)} / {formatBytes(info.total)}</span>
                </div>
                <div className="w-full h-2 bg-gray-200 dark:bg-gray-700 rounded-full overflow-hidden">
                    {showFill && (
                        <div
                            className={`h-full ${colorClass}`}
                            style={{ width: `${usedPercentage}%` }}
                        ></div>
                    )}
                </div>
            </div>
        </div>
    );
};

export default DiskSpaceIndicator; 