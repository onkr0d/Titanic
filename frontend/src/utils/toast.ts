import { Slide, toast, ToastOptions } from 'react-toastify';

const getCurrentTheme = (): 'light' | 'dark' => {
    return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
};

const getDefaultOptions = (): ToastOptions => ({
    position: "top-right",
    autoClose: 5000,
    hideProgressBar: false,
    closeOnClick: true,
    pauseOnFocusLoss: false,
    pauseOnHover: false,
    draggable: true,
    progress: undefined,
    transition: Slide,
    theme: getCurrentTheme(),
});

// Listen for theme changes
if (typeof window !== 'undefined') {
    window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
        // Force a re-render of all toasts by dismissing them
        toast.dismiss();
    });
}

export const showToast = {
    success: (message: string, options?: ToastOptions) => {
        toast.success(message, { ...getDefaultOptions(), ...options });
    },
    error: (message: string, options?: ToastOptions) => {
        toast.error(message, { ...getDefaultOptions(), ...options });
    },
    info: (message: string, options?: ToastOptions) => {
        toast.info(message, { ...getDefaultOptions(), ...options });
    },
    warning: (message: string, options?: ToastOptions) => {
        toast.warning(message, { ...getDefaultOptions(), ...options });
    },
}; 