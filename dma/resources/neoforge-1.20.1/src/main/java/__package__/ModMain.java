package {{package}};

import net.minecraftforge.common.MinecraftForge;
import net.minecraftforge.eventbus.api.IEventBus;
import net.minecraftforge.fml.common.Mod;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

@Mod({{class_name}}.MOD_ID)
public class {{class_name}} {
    public static final String MOD_ID = "{{mod_id}}";
    public static final Logger LOGGER = LoggerFactory.getLogger(MOD_ID);

    public {{class_name}}(IEventBus modEventBus) {
        LOGGER.info("{} initialized", MOD_ID);
        ModRegistries.register(modEventBus);
        MinecraftForge.EVENT_BUS.register(this);
    }
}
