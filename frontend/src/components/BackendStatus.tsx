import { useState, useEffect } from 'react';
import { Server, ServerOff } from 'lucide-react';
import { checkBackendHealth } from '../utils/api';

const BackendStatus = () => {
    const [isOnline, setIsOnline] = useState<boolean | null>(null);
    const [isLoading, setIsLoading] = useState(true);

    useEffect(() => {
        const checkStatus = async () => {
            try {
                const status = await checkBackendHealth();
                setIsOnline(status);
            } catch (error) {
                setIsOnline(false);
            } finally {
                setIsLoading(false);
            }
        };

        checkStatus();
        // Check status every 30 seconds
        const interval = setInterval(checkStatus, 30000);

        return () => clearInterval(interval);
    }, []);

    if (isLoading) {
        return (
            <div className="fixed bottom-4 right-4 flex items-center gap-2 px-3 py-2 bg-gray-100 dark:bg-gray-800 rounded-lg shadow-lg">
                <div className="w-4 h-4 border-2 border-t-blue-500 rounded-full animate-spin" />
                <span className="text-sm text-gray-600 dark:text-gray-300">Checking backend status...</span>
            </div>
        );
    }

    return (
        <div className="fixed bottom-4 right-4 flex items-center gap-2 px-3 py-2 bg-gray-100 dark:bg-gray-800 rounded-lg shadow-lg">
            {isOnline ? (
                <>
                    <Server className="w-4 h-4 text-green-500" />
                    <span className="text-sm text-gray-600 dark:text-gray-300">Backend online</span>
                </>
            ) : (
                <>
                    <ServerOff className="w-4 h-4 text-red-500" />
                    <span className="text-sm text-gray-600 dark:text-gray-300">Backend offline</span>
                </>
            )}
        </div>
    );
};

export default BackendStatus; 