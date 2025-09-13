import React, { useState, useRef, useEffect } from 'react';
import { UploadCloud, X, Check, Clapperboard, Folder } from 'lucide-react';
import { showToast } from '../utils/toast';
import { uploadVideo, getFolders } from '../utils/api';
import Tooltip from './Tooltip';
import { Switch } from './animate-ui/base/switch';

interface FileState {
    file: File;
    id: string;
    status: 'ready' | 'uploading' | 'uploaded' | 'error';
    error?: string;
    shouldCompress: boolean;
    folder?: string;
    progress?: number;
}

const FileUploader = () => {
    const [isDragging, setIsDragging] = useState(false);
    const [files, setFiles] = useState<FileState[]>([]);
    const [availableFolders, setAvailableFolders] = useState<string[]>([]);
    const [shiftPressed, setShiftPressed] = useState(false);
    const fileInputRef = useRef<HTMLInputElement>(null);

    // Fetch available folders on component mount
    useEffect(() => {
        const fetchFolders = async () => {
            const folders = await getFolders();
            setAvailableFolders(folders);
        };
        fetchFolders();

        // Add global keyboard event listeners
        const handleKeyDown = (e: KeyboardEvent) => {
            if (e.key === 'Shift') {
                setShiftPressed(true);
            }
        };

        const handleKeyUp = (e: KeyboardEvent) => {
            if (e.key === 'Shift') {
                setShiftPressed(false);
            }
        };

        // Try document instead of window for better event capturing
        document.addEventListener('keydown', handleKeyDown, true); // useCapture = true
        document.addEventListener('keyup', handleKeyUp, true);

        return () => {
            document.removeEventListener('keydown', handleKeyDown, true);
            document.removeEventListener('keyup', handleKeyUp, true);
        };
    }, []);

    const isValidVideoFile = (file: File) => {
        return file.type.startsWith('video/');
    };

    const handleDragEnter = (e: React.DragEvent<HTMLDivElement>) => {
        e.preventDefault();
        e.stopPropagation();
        setIsDragging(true);
    };

    const handleDragLeave = (e: React.DragEvent<HTMLDivElement>) => {
        e.preventDefault();
        e.stopPropagation();
        setIsDragging(false);
    };

    const handleDrop = (e: React.DragEvent<HTMLDivElement>) => {
        e.preventDefault();
        e.stopPropagation();
        setIsDragging(false);

        const droppedFiles = Array.from(e.dataTransfer.files);
        handleFiles(droppedFiles);
    };

    const handlePaste = (e: React.ClipboardEvent<HTMLDivElement>) => {
        const pastedFiles = Array.from(e.clipboardData.files);
        if (pastedFiles.length > 0) {
            handleFiles(pastedFiles);
        }
    };

    const handleFiles = (newFiles: File[]) => {
        const validFiles = newFiles.filter(isValidVideoFile);
        if (validFiles.length !== newFiles.length) {
            showToast.error('Some files were skipped because they are not valid video files');
        }
        setFiles(prev => [...prev, ...validFiles.map(file => ({
            file,
            id: Math.random().toString(36).substring(2, 11),
            status: 'ready' as const,
            shouldCompress: true, // Default to true for compression
            folder: "Clips", // Default to Clips folder
            progress: undefined
        }))]);
    };

    const removeFile = (fileId: string) => {
        setFiles(prev => prev.filter(f => f.id !== fileId));
    }

    const toggleCompression = (fileId: string) => {
        setFiles(prev => prev.map(f =>
            f.id === fileId ? { ...f, shouldCompress: !f.shouldCompress } : f
        ));
    };

    const setFileFolder = (fileId: string, folder: string, applyToAll: boolean = false) => {
        const finalApplyToAll = applyToAll || shiftPressed;

        if (finalApplyToAll) {
            // Apply to all files that are not yet uploaded
            setFiles(prev => prev.map(f =>
                f.status === 'ready' ? { ...f, folder: folder || "Clips" } : f
            ));
        } else {
            // Apply to just this file
            setFiles(prev => prev.map(f =>
                f.id === fileId ? { ...f, folder: folder || "Clips" } : f
            ));
        }
    };

    const uploadFiles = async () => {
        const readyFiles = files.filter(f => f.status === 'ready');
        if (readyFiles.length === 0) return;

        // Update status to uploading for all ready files
        setFiles(prev => prev.map(f =>
            f.status === 'ready' ? { ...f, status: 'uploading', progress: 0 } : f
        ));

        try {
            // Upload all files in parallel
            const uploadPromises = readyFiles.map(async ({ file, id, shouldCompress, folder }) => {
                const result = await uploadVideo(file, shouldCompress, folder, (progress) => {
                    setFiles(prev => prev.map(f =>
                        f.id === id ? { ...f, progress: progress.progress } : f
                    ));
                });

                if (result.success) {
                    // Update file status to uploaded
                    setFiles(prev => prev.map(f =>
                        f.id === id ? { ...f, status: 'uploaded', progress: 100 } : f
                    ));
                    showToast.success(`Successfully uploaded ${file.name}`, {
                        pauseOnFocusLoss: false,
                    });
                } else {
                    // Update file status to error
                    setFiles(prev => prev.map(f =>
                        f.id === id ? {
                            ...f,
                            status: 'error',
                            error: result.error
                        } : f
                    ));
                    showToast.error(`Failed to upload ${file.name}: ${result.error}`);
                }
            });

            await Promise.all(uploadPromises);
        } catch (error) {
            console.error('Upload error:', error);
            showToast.error('An error occurred during upload');
        }
    };

    return (
        <div className="w-full max-w-xl mx-auto p-6">
            <div
                className={`border-2 border-dashed rounded-lg p-8 ${isDragging
                    ? 'border-blue-500 bg-blue-50 dark:bg-blue-900/20'
                    : 'border-gray-300 dark:border-gray-700'
                    }`}
                onDragEnter={handleDragEnter}
                onDragOver={handleDragEnter}
                onDragLeave={handleDragLeave}
                onDrop={handleDrop}
                onPaste={handlePaste}
                onClick={() => fileInputRef.current?.click()}
            >
                <div className="flex flex-col items-center justify-center text-center">
                    <UploadCloud className="w-12 h-12 text-gray-400 dark:text-gray-500 mb-4" />
                    <h3 className="text-lg font-semibold mb-2 text-gray-900 dark:text-gray-100">
                        Drag & drop video files here
                    </h3>
                    <p className="text-sm text-gray-500 dark:text-gray-400 mb-4">
                        or click to select video files
                    </p>
                    <input
                        ref={fileInputRef}
                        type="file"
                        multiple
                        accept="video/*"
                        className="hidden"
                        onChange={(e) => handleFiles(Array.from(e.target.files || []))}
                    />
                </div>
            </div>

            {files.length > 0 && (
                <div className="mt-6">
                    <div className="space-y-3">
                        {files.map(({ file, id, status, error, shouldCompress, folder, progress }) => (
                            <div
                                key={id}
                                className="flex items-center justify-between p-3 bg-gray-50 dark:bg-gray-800 rounded-lg"
                            >
                                <div className="flex items-center space-x-3 flex-grow">
                                    <Clapperboard className="w-5 h-5 text-gray-400 dark:text-gray-500" />
                                    <div className="flex flex-col flex-grow">
                                        <span className="text-sm font-medium text-gray-900 dark:text-gray-100">{file.name}</span>
                                        {folder && folder !== "Clips" && (
                                            <span className="text-xs text-blue-600 dark:text-blue-400 flex items-center">
                                                <Folder className="w-3 h-3 mr-1" />
                                                {folder}
                                            </span>
                                        )}
                                        {folder === "Clips" && (
                                            <span className="text-xs text-gray-500 dark:text-gray-400 flex items-center">
                                                <Folder className="w-3 h-3 mr-1" />
                                                Clips
                                            </span>
                                        )}
                                        {error && (
                                            <span className="text-xs text-red-500 dark:text-red-400">{error}</span>
                                        )}
                                        {status === 'uploading' && progress !== undefined && (
                                            <div className="w-full h-1.5 bg-gray-200 dark:bg-gray-700 rounded-full overflow-hidden mt-1">
                                                <div
                                                    className="h-full bg-blue-500 transition-all duration-300"
                                                    style={{ width: `${progress}%` }}
                                                />
                                            </div>
                                        )}
                                    </div>
                                </div>
                                <div className="flex items-center space-x-2">
                                    {status === 'uploaded' ? (
                                        <Check className="w-5 h-5 text-green-500" />
                                    ) : status === 'uploading' ? (
                                        <div className="w-5 h-5 border-2 border-t-blue-500 rounded-full animate-spin" />
                                    ) : (
                                        <div className="flex items-center">
                                            <Tooltip content="Select destination folder (hold Shift to apply to all files)">
                                                <select
                                                    value={folder || "Clips"}
                                                    onChange={(e) => {
                                                        setFileFolder(id, e.target.value);
                                                    }}
                                                    className="text-xs bg-gray-200 dark:bg-gray-700 border border-gray-300 dark:border-gray-600 rounded px-2 py-1 focus:outline-none focus:ring-2 focus:ring-blue-500"
                                                    onClick={(e) => e.stopPropagation()}
                                                >
                                                    <option value="Clips">Clips</option>
                                                    {availableFolders.map(folderName => (
                                                        <option key={folderName} value={folderName}>
                                                            {folderName}
                                                        </option>
                                                    ))}
                                                </select>
                                            </Tooltip>
                                            <Tooltip
                                                content={
                                                    shouldCompress
                                                        ? "Smart compression enabled"
                                                        : "Compression disabled: clip will appear in Plex as is"
                                                }
                                            >
                                                <Switch
                                                    checked={shouldCompress}
                                                    onCheckedChange={() => toggleCompression(id)}
                                                    className="data-[checked]:bg-green-500 data-[unchecked]:bg-gray-300 dark:data-[unchecked]:bg-gray-600 self-center my-1 scale-75"
                                                />
                                            </Tooltip>
                                            <button
                                                onClick={(e) => {
                                                    e.stopPropagation();
                                                    removeFile(id);
                                                }}
                                                className="p-1 hover:bg-gray-200 dark:hover:bg-gray-700 rounded my-auto"
                                            >
                                                <X className="w-4 h-4 text-gray-500" />
                                            </button>
                                        </div>
                                    )}
                                </div>
                            </div>
                        ))}
                    </div>

                    {files.some(f => f.status === 'ready') && (
                        <button
                            onClick={(e) => {
                                e.stopPropagation();
                                uploadFiles();
                            }}
                            className="mt-4 px-4 py-2 bg-blue-500 text-white rounded hover:bg-blue-600 dark:hover:bg-blue-700 w-full"
                        >
                            Upload Files
                        </button>
                    )}
                </div>
            )}
        </div>
    );
};

export default FileUploader;