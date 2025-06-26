import { beforeUserCreated} from "firebase-functions/v2/identity";
import * as logger from "firebase-functions/logger";
import { HttpsError } from "firebase-functions/v2/identity";

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
    throw new HttpsError('permission-denied', 'you are not a fish, nor a squid!');
});