import { AuthError, User } from "firebase/auth";
import { onAuthStateChanged, GoogleAuthProvider, signInWithPopup } from "firebase/auth";
import { getAuth, connectAuthEmulator} from "firebase/auth";
import FileUploader from "./components/FileUploader";

// Import the functions you need from the SDKs you need
import { useState } from "react";
import { initializeApp } from "firebase/app";
import { useEffect } from "react";
import { Slide, toast, ToastContainer } from "react-toastify";
// TODO: Add SDKs for Firebase products that you want to use
// https://firebase.google.com/docs/web/setup#available-libraries

// Your web app's Firebase configuration
const firebaseConfig = {
  apiKey: "how'd that get here",
  authDomain: "oops",
  projectId: "probably not critical",
  storageBucket: "probably not critical.firebasestorage.app",
  messagingSenderId: "yikes",
  appId: "1:yikes:web:419dffceedf0eaaddb4fd8"
};

// Initialize Firebase
// const app = 
initializeApp(firebaseConfig);

const App = () => {
    const [user, setUser] = useState<User | null>(null);

    useEffect(() => {
        const auth = getAuth();
        connectAuthEmulator(auth, "http://127.0.0.1:9099");
        onAuthStateChanged(auth, (user) => {
            setUser(user);
        });
    }, []);

    const signInWithGoogle = async () => {
        const auth = getAuth();
        const provider = new GoogleAuthProvider();
        try {
            const result = await signInWithPopup(auth, provider);
            console.log("User email:", result.user.email);
        } catch (error: any) {
            let errorMessage = "Error signing in with Google";
            
            // Check if it's a Firebase Function error
            if (error.code === 'auth/internal-error' && error.message.includes('HTTP error 403')) {
                try {
                    // Extract the error message from the function response
                    const match = error.message.match(/{"error":{"message":"([^"]+)"/);
                    if (match) {
                        errorMessage = "Server error: " + match[1];
                    }
                } catch (e) {
                    console.error("Error parsing error message:", e);
                }
            }

            toast.error(errorMessage, {
                position: "top-right",
                autoClose: 5000,
                hideProgressBar: false,
                closeOnClick: true,
                pauseOnFocusLoss: false,
                pauseOnHover: false,
                draggable: true,
                progress: undefined,
                theme: window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light',
                transition: Slide,
            });
        }
    };

// getAuth().signOut();

    if (!user) {
        return (
            <div>
            <ToastContainer draggablePercent={60}/>
            <div className="min-h-screen w-full flex items-center justify-center">
                <button
                    onClick={signInWithGoogle}
                    className="flex items-center gap-2 bg-white text-gray-700 px-6 py-3 rounded-lg border border-gray-300 hover:bg-gray-50 transition-colors shadow-sm"
                >
                    <svg className="w-5 h-5" viewBox="0 0 24 24">
                        <path
                            fill="currentColor"
                            d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"
                        />
                        <path
                            fill="currentColor"
                            d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"
                        />
                        <path
                            fill="currentColor"
                            d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z"
                        />
                        <path
                            fill="currentColor"
                            d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"
                        />
                    </svg>
                    Continue with Google
                </button>
            </div>
            </div>
        );
    }

    return (
        <div className="min-h-screen w-full flex items-center justify-center">
            <FileUploader/>
        </div>
    );
};

export default App;