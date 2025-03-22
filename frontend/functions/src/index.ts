/**
 * Import function triggers from their respective submodules:
 *
 * import {onCall} from "firebase-functions/v2/https";
 * import {onDocumentWritten} from "firebase-functions/v2/firestore";
 *
 * See a full list of supported triggers at https://firebase.google.com/docs/functions
 */

import {onRequest} from "firebase-functions/v2/https";
import { beforeUserCreated} from "firebase-functions/v2/identity";
import * as logger from "firebase-functions/logger";
import { HttpsError } from "firebase-functions/v2/identity";

// Start writing functions
// https://firebase.google.com/docs/functions/typescript

export const helloWorld = onRequest((request, response) => {
  logger.info("Hello logs!", {structuredData: true});
  response.send("Hello from Firebase!");
});

export const onlyAuthUsers = beforeUserCreated((event) => {
    const email = event.data?.email;
    if (!email) {
        logger.error("No email provided");
        throw new HttpsError('permission-denied', 'No email provided');
    }

    const allowedEmails = process.env.ALLOWED_EMAILS?.split(',') || [];

    if (allowedEmails.includes(email)) {
        logger.info("User email:", email);
        return;
    }
    logger.error("Unauthorized user: " + email);
    throw new HttpsError('permission-denied', 'You are not a fish, nor a squid!');
});