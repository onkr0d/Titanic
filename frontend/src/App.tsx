import { User } from "firebase/auth";
import { onAuthStateChanged, GoogleAuthProvider, signInWithPopup } from "firebase/auth";
import { getAuth, connectAuthEmulator } from "firebase/auth";
import FileUploader from "./components/FileUploader";
import DiskSpaceIndicator from "./components/DiskSpaceIndicator";
import { initializeAppCheck, ReCaptchaEnterpriseProvider } from "firebase/app-check";

// Import the functions you need from the SDKs you need
import { useState } from "react";
import { initializeApp } from "firebase/app";
import { useEffect } from "react";
import { Slide, ToastContainer } from "react-toastify";
import { showToast } from "./utils/toast";
// TODO: Add SDKs for Firebase products that you want to use
// https://firebase.google.com/docs/web/setup#available-libraries

// Your web app's Firebase configuration
const firebaseConfig = {
    apiKey: import.meta.env.VITE_FIREBASE_API_KEY,
    authDomain: import.meta.env.VITE_FIREBASE_AUTH_DOMAIN,
    projectId: import.meta.env.VITE_FIREBASE_PROJECT_ID,
    storageBucket: import.meta.env.VITE_FIREBASE_STORAGE_BUCKET,
    messagingSenderId: import.meta.env.VITE_FIREBASE_MESSAGING_SENDER_ID,
    appId: import.meta.env.VITE_FIREBASE_APP_ID
};

// Initialize Firebase
const app = initializeApp(firebaseConfig);

// Initialize App Check with proper debug mode handling
export const appCheck = initializeAppCheck(app, {
    provider: new ReCaptchaEnterpriseProvider(import.meta.env.DEV ? import.meta.env.VITE_FIREBASE_RECAPTCHA_SITE_KEY_DEV : import.meta.env.VITE_FIREBASE_RECAPTCHA_SITE_KEY),
    isTokenAutoRefreshEnabled: true,
});

// Log App Check status for debugging
if (import.meta.env.DEV) {
    console.log('Firebase App Check initialized in development mode');
}

const App = () => {
    const [user, setUser] = useState<User | null>(null);

    useEffect(() => {
        const auth = getAuth();
        // use emulator if in dev
        if (import.meta.env.DEV) {
            connectAuthEmulator(auth, "http://127.0.0.1:9099");
        }
        onAuthStateChanged(auth, (user) => {
            setUser(user);
        });
    }, []);

    const signInWithGoogle = async () => {
        const auth = getAuth();
        const provider = new GoogleAuthProvider();
        try {
            await signInWithPopup(auth, provider);
            showToast.success("Signed in with Google", {
                transition: Slide,
            });
        } catch (error: any) {
            let errorMessage = "Error signing in with Google";

            if (error.code === 'auth/internal-error' && error.message.includes('HTTP error 403')) {
                try {
                    // Try to parse the error message as JSON
                    const errorMatch = error.message.match(/HTTP Cloud Function returned an error: (.+)/);
                    if (errorMatch) {
                        const functionError = JSON.parse(errorMatch[1]);
                        if (functionError.error && functionError.error.message) {
                            errorMessage += ": " + functionError.error.message;
                        }
                    }
                } catch (e) {
                    console.error("Error parsing error message:", e);
                }
            }

            showToast.error(errorMessage, {
                transition: Slide,
            });
        }
    };

    if (!user) {
        return (
            <div className="bg-white dark:bg-gray-900 min-h-screen">
                <ToastContainer draggablePercent={60} />
                <div className="min-h-screen w-full flex items-center justify-center">
                    <button
                        onClick={signInWithGoogle}
                        className="flex items-center gap-2 bg-white dark:bg-gray-800 text-gray-700 dark:text-gray-200 px-6 py-3 rounded-lg border border-gray-300 dark:border-gray-700 hover:bg-gray-50 dark:hover:bg-gray-700 transition-colors shadow-sm"
                    >
                        <img src="/google-icon-logo-svgrepo-com.svg" alt="Google" className="w-5 h-5" />
                        Continue with Google
                    </button>
                </div>
            </div>
        );
    }

    return (
        <div className="bg-white dark:bg-gray-900 min-h-screen">
            <ToastContainer draggablePercent={60} />
            <div className="min-h-screen w-full flex items-center justify-center">
                <FileUploader />
            </div>
            <div className="fixed bottom-4 left-4 right-4 md:left-auto md:right-4 z-10">
                <div className="md:hidden space-y-2 max-w-[50vw]">
                    <DiskSpaceIndicator />
                </div>
                <div className="hidden md:block">
                    <DiskSpaceIndicator />
                </div>
            </div>
        </div>
    );
};

export default App;