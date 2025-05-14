import { ReactNode } from 'react';

interface TooltipProps {
    children: ReactNode;
    content: string;
}

const Tooltip = ({ children, content }: TooltipProps) => {
    return (
        <div className="group relative inline-block">
            {children}
            <div className="absolute bottom-full left-1/2 -translate-x-1/2 mb-2 px-2 py-1 text-xs text-white bg-gray-900 dark:bg-gray-700 rounded opacity-0 group-hover:opacity-100 transition-opacity whitespace-nowrap pointer-events-none">
                {content}
                <div className="absolute top-full left-1/2 -translate-x-1/2 -mt-1 border-4 border-transparent border-t-gray-900 dark:border-t-gray-700"></div>
            </div>
        </div>
    );
};

export default Tooltip; 