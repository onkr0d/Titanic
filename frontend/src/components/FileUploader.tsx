import React, { useState, useRef } from 'react';
import { UploadCloud, X, FileText, CheckCircle } from 'lucide-react';

interface FileState {
    file: File;
    id: string;
    status: 'ready' | 'uploading' | 'uploaded';
}

const FileUploader = () => {
    const [isDragging, setIsDragging] = useState(false);
    const [files, setFiles] = useState<FileState[]>([]);
    const fileInputRef = useRef<HTMLInputElement>(null);

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
        setFiles(prev => [...prev, ...newFiles.map(file => ({
            file,
            id: Math.random().toString(36).substr(2, 9),
            status: 'ready' as const
        }))]);
    };

    const removeFile = (fileId: string) => {
        setFiles(prev => prev.filter(f => f.id !== fileId));
    }

    const uploadFiles = async () => {
        // Simulate file upload - replace with actual upload logic
        setFiles(prev => prev.map(f => ({ ...f, status: 'uploading' })));

        await new Promise(resolve => setTimeout(resolve, 1500));

        setFiles(prev => prev.map(f => ({ ...f, status: 'uploaded' })));
    };

    return (
        <div className="w-full max-w-xl mx-auto p-6">
            <div
                className={`border-2 border-dashed rounded-lg p-8 ${
                    isDragging ? 'border-blue-500 bg-blue-50' : 'border-gray-300'
                }`}
                onDragEnter={handleDragEnter}
                onDragOver={handleDragEnter}
                onDragLeave={handleDragLeave}
                onDrop={handleDrop}
                onPaste={handlePaste}
                onClick={() => fileInputRef.current?.click()}
            >
                <div className="flex flex-col items-center justify-center text-center">
                    <UploadCloud className="w-12 h-12 text-gray-400 mb-4" />
                    <h3 className="text-lg font-semibold mb-2">
                        Drag & drop files here
                    </h3>
                    <p className="text-sm text-gray-500 mb-4">
                        or click to select files
                    </p>
                    <input
                        ref={fileInputRef}
                        type="file"
                        multiple
                        className="hidden"
                        onChange={(e) => handleFiles(Array.from(e.target.files || []))}
                    />
                </div>
            </div>

            {files.length > 0 && (
                <div className="mt-6">
                    <div className="space-y-3">
                        {files.map(({ file, id, status }) => (
                            <div
                                key={id}
                                className="flex items-center justify-between p-3 bg-gray-50 rounded-lg"
                            >
                                <div className="flex items-center space-x-3">
                                    <FileText className="w-5 h-5 text-gray-400" />
                                    <span className="text-sm font-medium text-gray-900">{file.name}</span>
                                </div>
                                <div className="flex items-center space-x-2">
                                    {status === 'uploaded' ? (
                                        <CheckCircle className="w-5 h-5 text-green-500" />
                                    ) : status === 'uploading' ? (
                                        <div className="w-5 h-5 border-2 border-t-blue-500 rounded-full animate-spin" />
                                    ) : (
                                        <button
                                            onClick={(e) => {
                                                e.stopPropagation();
                                                removeFile(id);
                                            }}
                                            className="p-1 hover:bg-gray-200 rounded"
                                        >
                                            <X className="w-4 h-4 text-gray-500" />
                                        </button>
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
                            className="mt-4 px-4 py-2 bg-blue-500 text-white rounded hover:bg-blue-600 w-full"
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