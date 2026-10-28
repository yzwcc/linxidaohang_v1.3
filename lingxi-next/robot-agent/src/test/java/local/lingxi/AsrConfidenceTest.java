package local.lingxi;

import com.agibot.aiem.sdk.AgentParam;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

class AsrConfidenceTest {
    @Test void acceptsOnlyFiniteNumericConfidenceWithinTheDocumentedScale() {
        assertEquals(0.78, AsrConfidence.read(AgentParam.create().setDouble("confidence", 0.78)).value());
        assertEquals(1.0, AsrConfidence.read(AgentParam.create().setInteger("confidence", 1)).value());
        assertEquals(0.0, AsrConfidence.read(AgentParam.create().setDouble("confidence", 0.0)).value());
    }

    @Test void distinguishesMissingFromMalformedConfidenceWithoutCoercion() {
        assertEquals("asr_confidence_unavailable", AsrConfidence.read(null).rejection());
        assertEquals("asr_confidence_unavailable", AsrConfidence.read(AgentParam.create()).rejection());
        assertEquals("asr_confidence_invalid",
                AsrConfidence.read(AgentParam.create().setString("confidence", "0.99")).rejection());
        assertEquals("asr_confidence_invalid",
                AsrConfidence.read(AgentParam.create().setDouble("confidence", Double.NaN)).rejection());
        assertEquals("asr_confidence_invalid",
                AsrConfidence.read(AgentParam.create().setDouble("confidence", Double.POSITIVE_INFINITY)).rejection());
        assertEquals("asr_confidence_invalid",
                AsrConfidence.read(AgentParam.create().setDouble("confidence", -0.01)).rejection());
        assertEquals("asr_confidence_invalid",
                AsrConfidence.read(AgentParam.create().setDouble("confidence", 1.01)).rejection());
    }
}
