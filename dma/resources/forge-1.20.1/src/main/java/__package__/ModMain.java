package {{package}};

import com.mojang.logging.LogUtils;
import net.minecraftforge.fml.common.Mod;
import net.minecraftforge.fml.javafmlmod.FMLJavaModLoadingContext;
import org.slf4j.Logger;

@Mod(ModMain.MODID)
public final class ModMain {
    public static final String MODID = "{{mod_id}}";
    private static final Logger LOGGER = LogUtils.getLogger();

    public ModMain() {
        LOGGER.info("{} loaded", MODID);
        ModRegistries.register(FMLJavaModLoadingContext.get().getModEventBus());
    }
}
