package local.lingxi;

import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

class SpeechRequestsTest {
    @Test void cancelBeforeStartRejectsEveryLaterSegment() {
        var requests = new SpeechRequests();
        requests.cancel("reply:s:e", 100_000);
        assertThrows(IllegalStateException.class, () -> requests.check("reply:s:e", 100_001, 100_002, true));
        assertThrows(IllegalStateException.class, () -> requests.check("reply:s:e", 100_050, 100_051, true));
        assertDoesNotThrow(() -> requests.check("reply:s:new", 100_050, 100_051, true));
    }
    @Test void passiveReplyCannotFallBackToTaskflowWhenContextIsGone() {
        var requests = new SpeechRequests();
        assertThrows(IllegalStateException.class, () -> requests.check("reply:s:e", 100_000, 100_001, false));
        assertDoesNotThrow(() -> requests.check("tour-unique", 100_000, 100_001, false));
    }
    @Test void staleNetworkRequestsRemainRejectedAfterCancellationRecordExpires() {
        var requests = new SpeechRequests();
        requests.cancel("tour", 100_000);
        assertThrows(IllegalStateException.class, () -> requests.check("tour", 100_001, 800_000, false));
        assertThrows(IllegalStateException.class, () -> requests.check("tour", 802_001, 800_000, false));
    }
}
