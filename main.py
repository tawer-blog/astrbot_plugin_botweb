import asyncio
from typing import Optional
import re

import astrbot.api.star as star
from astrbot.api import llm_tool, logger
from astrbot.api.event import AstrMessageEvent

try:
    import httpx
except Exception:
    httpx = None

try:
    from ddgs import DDGS
except Exception:
    DDGS = None


class Main(star.Star):
    def __init__(self, context: star.Context, config=None) -> None:
        self.context = context
        self.config = config or {}

    async def initialize(self):
        self.context.activate_llm_tool("sousuo_search")
        self.context.activate_llm_tool("sousuo_fetch")
        logger.info("[sousuo_search] 函数工具已启用")
        logger.info("[sousuo_fetch] 函数工具已启用")

    @llm_tool("sousuo_search")
    async def sousuo_search(self, event: AstrMessageEvent, query: str) -> str:
        """这是一个“联网搜索”的函数工具（工具名：sousuo_search）。当需要获取互联网上的实时/最新信息时，你必须调用本工具进行搜索。

        Args:
            query(string): 简要说明用户希望检索的查询内容

        Returns:
            str: 要点摘要与引用来源，作为 tool 消息注入上下文
        """
        if DDGS is None:
            return "插件缺少依赖 ddgs，请在该插件 requirements.txt 中安装后重启。"

        try:
            search_results = await self._perform_search(query)
            if not search_results:
                return "未找到相关搜索结果。"

            formatted_results = self._format_search_results(search_results)
            return formatted_results
        except Exception as e:
            logger.error(f"[sousuo_search] 搜索失败: {e}")
            return f"搜索失败：{str(e)}"

    @llm_tool("sousuo_fetch")
    async def sousuo_fetch(self, event: AstrMessageEvent, url: str) -> str:
        """抓取网页文本内容（去标签纯文本）。

        Args:
            url(string): 需要抓取内容的网页 URL

        Returns:
            str: 提取的纯文本（前 20,000 字符内），失败时返回错误信息
        """
        if httpx is None:
            return "插件缺少依赖 httpx，请在该插件 requirements.txt 中安装后重启。"
        try:
            text = await self._fetch_page_text(url)
            if not text:
                return "未能从页面中提取到有效文本。"
            max_chars = 20000
            return text[:max_chars]
        except Exception as e:
            logger.error(f"[sousuo_fetch] 抓取失败: {e}")
            return f"抓取失败：{e}"

    async def _perform_search(self, query: str) -> Optional[list]:
        max_results = int(self.config.get("max_results", 10))
        
        search_params = {
            "region": "cn-zh",
            "safesearch": "off",
            "max_results": max_results,
            "backend": "bing"
        }
        
        priority_domains = [
            "baike.baidu.com",
            "baike.sogou.com",
            "www.wikipedia.org",
            "www.zhihu.com",
            "mzh.moegirl.org.cn",
            "www.3dmgame.com",
            "news.163.com",
            "www.gamersky.com",
            "store.steampowered.com"
        ]
        
        results = []
        for retry in range(3):
            try:
                with DDGS() as ddgs:
                    search_results = list(ddgs.text(
                        query=query,
                        region=search_params["region"],
                        safesearch=search_params["safesearch"],
                        max_results=search_params["max_results"],
                        backend=search_params["backend"]
                    ))
                
                if len(search_results) > 0:
                    sorted_results = self._sort_by_priority(search_results, priority_domains)
                    
                    formatted_results = []
                    for idx, result in enumerate(sorted_results, 1):
                        formatted_results.append({
                            "rank": idx,
                            "title": result.get('title', ''),
                            "url": result.get('href', ''),
                            "description": result.get('body', ''),
                            "source": result.get('source', '')
                        })
                    
                    logger.info(f"[sousuo_search] 搜索关键词「{query}」成功，返回{len(formatted_results)}条结果")
                    return formatted_results
                else:
                    if retry < 2:
                        await asyncio.sleep(6)
            except Exception as e:
                logger.error(f"[sousuo_search] 搜索关键词「{query}」第{retry+1}次尝试失败：{str(e)}")
                if retry < 2:
                    await asyncio.sleep(6)
        
        logger.info(f"[sousuo_search] 搜索关键词「{query}」所有尝试均返回0条结果")
        return []

    def _sort_by_priority(self, results: list, priority_domains: list) -> list:
        def get_priority_score(result):
            url = result.get('href', '')
            for domain in priority_domains:
                if domain in url:
                    return 0
            return 1
        
        return sorted(results, key=get_priority_score)

    def _format_search_results(self, results: list) -> str:
        if not results:
            return "未找到相关结果。"
        
        formatted = "搜索结果摘要：\n\n"
        max_results = min(len(results), 5)
        
        for i, result in enumerate(results[:max_results], 1):
            rank = result.get("rank", i)
            title = result.get("title", "无标题").strip()
            url = result.get("url", "").strip()
            description = result.get("description", "无描述").strip()
            
            max_desc_len = 200
            if len(description) > max_desc_len:
                description = description[:max_desc_len] + "..."
            
            formatted += f"{rank}. {title}\n"
            formatted += f"   描述: {description}\n"
            formatted += f"   链接: {url}\n\n"
        
        formatted += f"共找到 {len(results)} 条结果，显示前 {max_results} 条。"
        return formatted

    async def _fetch_page_text(self, url: str) -> Optional[str]:
        timeout = float(self.config.get("fetch_timeout_seconds", 20))
        headers = {"User-Agent": "Mozilla/5.0 AstrBot"}
        
        try:
            async with httpx.AsyncClient(timeout=timeout, headers=headers, follow_redirects=True) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                html = resp.text
                
                html = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL)
                html = re.sub(r'<style[^>]*>.*?</style>', '', html, flags=re.DOTALL)
                text = re.sub(r'<[^>]+>', ' ', html)
                text = re.sub(r'\s+', ' ', text).strip()
                
                return text
        except Exception as e:
            logger.error(f"[sousuo_fetch] 抓取网页失败: {e}")
            raise
