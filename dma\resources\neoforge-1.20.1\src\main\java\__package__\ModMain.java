package {{package}};

import net.neoforged.bus.api.IEventBus;
import net.neoforged.fml.common.Mod;
import net.neoforged.neoforge.common.NeoForge;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

@Mod({{class_name}}.MOD_ID)
public class {{class_name}} {
    public static final String MOD_ID = "{{mod_id}}";
    public static final Logger LOGGER = LoggerFactory.getLogger(MOD_ID);

    public {{class_name}}(IEventBus modEventBus) {
        LOGGER.info("{} initialized", MOD_ID);
        NeoForge.EVENT_BUS.register(this);
    }
}
